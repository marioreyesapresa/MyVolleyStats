"""Pruebas de aislamiento multi-entrenador.

Estas pruebas usan la base de datos de pruebas que Django crea y destruye
automáticamente (nunca tocan db.sqlite3). Ejecutar con:

    python manage.py test stats_app
"""
import json
import threading
from datetime import date, time, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import OperationalError, connection
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import CaptureQueriesContext
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .db_utils import reintentar_en_error_transitorio
from .forms import RegistrarAccionForm, RegistrarCambioForm, EliminarAccionForm
from .models import Equipo, Jugadora, Partido, RegistroEstadistica, RotacionSet, NotaPartido, LineupPreset
from .security import RateLimitMiddleware
from .services.reporting import (
    build_full_report,
    build_quick_report,
    build_advanced_report,
    advanced_player_row,
    build_run_chart,
    build_set_leaders,
    build_destacados_por_accion,
    build_quick_set_report,
    trazo_analysis,
    build_set_report,
    build_partido_snapshot,
    calc_racha,
    calc_racha_maxima,
    calc_set_score,
    marcador_resumen,
    merito_y_error_rival,
    _candidata_cambio,
)
from .services.temporada import stats_jugadora_temporada, stats_temporada_equipo
from .services.plantilla_csv import (
    normalizar_posicion,
    parsear_plantilla_csv,
)

User = get_user_model()


class AislamientoEntrenadorTests(TestCase):
    """Un entrenador nunca debe poder ver ni modificar datos de otro."""

    def setUp(self):
        self.coach_a = User.objects.create_user(username='coach_a', password='pass12345')
        self.coach_b = User.objects.create_user(username='coach_b', password='pass12345')

        self.equipo_a = Equipo.objects.create(
            entrenador=self.coach_a, nombre='Equipo A', temporada='2025/2026', categoria='SENIOR'
        )
        self.equipo_b = Equipo.objects.create(
            entrenador=self.coach_b, nombre='Equipo B', temporada='2025/2026', categoria='SENIOR'
        )

        self.jugadora_b = Jugadora.objects.create(
            equipo=self.equipo_b, nombre='Ana', apellidos='Pérez', dorsal=4, posicion='CENTRAL'
        )

        self.partido_b = Partido.objects.create(
            equipo=self.equipo_b, fecha=date(2026, 1, 10), hora=time(18, 0),
            rival='Rival B', local=True, lugar='Pabellón B',
        )

    def login_a(self):
        self.client.login(username='coach_a', password='pass12345')

    # ── Dashboard y listados ─────────────────────────────────────────────
    def test_dashboard_no_muestra_partidos_ajenos(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertNotContains(response, 'Rival B')

    def test_equipos_list_no_muestra_equipos_ajenos(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:equipos_list'))
        self.assertNotContains(response, 'Equipo B')

    # ── CRUD: editar/eliminar recursos ajenos debe dar 404 ──────────────
    def test_editar_equipo_ajeno_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:equipo_editar', args=[self.equipo_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_exportar_csv_de_equipo_ajeno_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:equipo_exportar_csv', args=[self.equipo_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_importar_csv_en_equipo_ajeno_da_404_y_no_crea_jugadoras(self):
        self.login_a()
        csv_bytes = b'dorsal,nombre,apellidos,posicion\n9,Hacker,IDOR,C\n'
        response = self.client.post(
            reverse('stats_app:equipo_importar_csv', args=[self.equipo_b.pk]),
            data={'archivo': SimpleUploadedFile('plantilla.csv', csv_bytes, content_type='text/csv')},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Jugadora.objects.filter(equipo=self.equipo_b, dorsal=9).exists())

    def test_ficha_jugadora_ajena_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:jugadora_ficha', args=[self.jugadora_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_eliminar_partido_ajeno_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:partido_eliminar', args=[self.partido_b.pk]))
        self.assertEqual(response.status_code, 404)

    # ── Modo partido y estadísticas ──────────────────────────────────────
    def test_modo_partido_ajeno_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:modo_partido', args=[self.partido_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_stats_final_ajeno_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:partido_stats_final', args=[self.partido_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_pdf_resumen_ajeno_da_404(self):
        self.login_a()
        response = self.client.get(reverse('stats_app:descargar_resumen_pdf', args=[self.partido_b.pk]))
        self.assertEqual(response.status_code, 404)

    # ── APIs: no se puede registrar sobre un partido ajeno ──────────────
    def test_registrar_accion_sobre_partido_ajeno_da_404(self):
        self.login_a()
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido_b.id,
                'jugadora_id': self.jugadora_b.id,
                'fase': 'K1',
                'accion': 'ATAQUE',
                'calidad': '++',
                'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_obtener_stats_set_sobre_partido_ajeno_da_404(self):
        self.login_a()
        response = self.client.post(
            reverse('stats_app:api_obtener_stats_set'),
            data=json.dumps({'partido_id': self.partido_b.id, 'set_numero': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_finalizar_partido_ajeno_da_404(self):
        self.login_a()
        response = self.client.post(
            reverse('stats_app:api_finalizar_partido', args=[self.partido_b.id])
        )
        self.assertEqual(response.status_code, 404)

    # ── Un entrenador SÍ puede operar sobre sus propios datos ───────────
    def test_entrenador_puede_ver_su_propio_partido(self):
        equipo_a2 = Equipo.objects.create(
            entrenador=self.coach_a, nombre='Equipo A2', temporada='2025/2026', categoria='SENIOR'
        )
        partido_a = Partido.objects.create(
            equipo=equipo_a2, fecha=date(2026, 1, 10), hora=time(18, 0),
            rival='Rival A', local=True, lugar='Pabellón A',
        )
        self.login_a()
        response = self.client.get(reverse('stats_app:modo_partido', args=[partido_a.pk]))
        self.assertEqual(response.status_code, 200)

    def test_creacion_equipo_asigna_entrenador_autenticado(self):
        self.login_a()
        self.client.post(reverse('stats_app:equipo_nuevo'), data={
            'nombre': 'Nuevo Equipo', 'temporada': '2025/2026', 'categoria': 'SENIOR',
        })
        nuevo = Equipo.objects.get(nombre='Nuevo Equipo')
        self.assertEqual(nuevo.entrenador, self.coach_a)


class RegistroEntrenadorTests(TestCase):
    """Registro público de nuevos entrenadores."""

    def test_registro_crea_usuario_y_redirige_al_dashboard(self):
        response = self.client.post(reverse('register'), {
            'username': 'nuevo_coach',
            'email': 'nuevo@example.com',
            'password1': 'ContraseñaSegura123!',
            'password2': 'ContraseñaSegura123!',
            'acepto_legal': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('stats_app:dashboard'))
        self.assertTrue(User.objects.filter(username='nuevo_coach').exists())

    def test_registro_rechaza_email_duplicado(self):
        User.objects.create_user(username='existente', email='dup@example.com', password='pass12345')
        response = self.client.post(reverse('register'), {
            'username': 'otro',
            'email': 'dup@example.com',
            'password1': 'ContraseñaSegura123!',
            'password2': 'ContraseñaSegura123!',
            'acepto_legal': 'on',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='otro').exists())

    def test_registro_exige_aceptacion_legal(self):
        response = self.client.post(reverse('register'), {
            'username': 'sin_aceptar',
            'email': 'sin@example.com',
            'password1': 'ContraseñaSegura123!',
            'password2': 'ContraseñaSegura123!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='sin_aceptar').exists())


class PaginasLegalesTests(TestCase):
    """Política de privacidad y términos son públicos."""

    def test_privacidad_accesible_sin_login(self):
        response = self.client.get(reverse('privacidad'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Política de Privacidad')

    def test_terminos_accesible_sin_login(self):
        response = self.client.get(reverse('terminos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Términos de Servicio')


# ═════════════════════════════════════════════════════════════════════════════
# TESTS DE INTRUSIÓN Y SEGURIDAD
#
# Simulan ataques reales contra "MyVolleyStats" para verificar que las
# capas de blindaje (validación de entrada, aislamiento por entrenador,
# rate limiting y resiliencia de base de datos) funcionan de extremo a
# extremo, no solo sobre el papel. Organizados por categoría OWASP.
# ═════════════════════════════════════════════════════════════════════════════

def _crear_entrenador_con_partido(username):
    """Helper: crea entrenador + equipo + jugadora + partido de una tacada."""
    coach = User.objects.create_user(username=username, password='pass12345')
    equipo = Equipo.objects.create(
        entrenador=coach, nombre=f'Equipo {username}', temporada='2025/2026', categoria='SENIOR'
    )
    jugadora = Jugadora.objects.create(
        equipo=equipo, nombre='Val', apellidos='Con', dorsal=7, posicion='OPUESTA'
    )
    partido = Partido.objects.create(
        equipo=equipo, fecha=date(2026, 2, 1), hora=time(18, 0),
        rival='Rival', local=True, lugar='Pabellón',
    )
    return coach, equipo, jugadora, partido


class InyeccionSQLyXSSTests(TestCase):
    """OWASP A03:2021 - Injection.

    El ORM de Django parametriza siempre sus queries (no hay SQL crudo en
    el proyecto), por lo que la inyección SQL clásica no es estructuralmente
    posible; estos tests son de regresión: verifican que un payload
    malicioso se guarda literal (nunca se "ejecuta" contra la BD) y que,
    al mostrarse en cualquier plantilla, Django lo escapa automáticamente
    (protección XSS por defecto de los templates).
    """

    PAYLOADS_XSS = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert(1)>",
        "'\"><svg/onload=alert(1)>",
    ]
    PAYLOADS_SQLI = [
        "'; DROP TABLE stats_app_equipo; --",
        "' OR '1'='1",
        "1) UNION SELECT username, password FROM auth_user--",
    ]

    def setUp(self):
        cache.clear()
        self.coach = User.objects.create_user(username='coach_xss', password='pass12345')
        self.client.login(username='coach_xss', password='pass12345')

    def test_nombre_equipo_con_xss_se_guarda_literal_y_se_escapa_en_listado(self):
        payload = self.PAYLOADS_XSS[0]
        self.client.post(reverse('stats_app:equipo_nuevo'), data={
            'nombre': payload, 'temporada': '2025/2026', 'categoria': 'SENIOR',
        })
        equipo = Equipo.objects.get(entrenador=self.coach)
        # Se almacena literal: el ORM no "interpreta" el payload como código.
        self.assertEqual(equipo.nombre, payload)

        response = self.client.get(reverse('stats_app:equipos_list'))
        self.assertNotContains(response, payload)
        self.assertContains(response, '&lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;')

    def test_rival_partido_con_xss_se_escapa_en_dashboard(self):
        equipo = Equipo.objects.create(
            entrenador=self.coach, nombre='Eq', temporada='2025/2026', categoria='SENIOR'
        )
        payload = self.PAYLOADS_XSS[1]
        Partido.objects.create(
            equipo=equipo, fecha=date(2026, 3, 1), hora=time(18, 0),
            rival=payload, local=True, lugar='Pabellón',
        )
        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertNotContains(response, payload)
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')

    def test_payloads_sqli_en_nombre_jugadora_se_guardan_literales(self):
        equipo = Equipo.objects.create(
            entrenador=self.coach, nombre='Eq', temporada='2025/2026', categoria='SENIOR'
        )
        for i, payload in enumerate(self.PAYLOADS_SQLI):
            self.client.post(reverse('stats_app:jugadora_nueva'), data={
                'equipo': equipo.id, 'nombre': payload, 'apellidos': 'Test',
                'dorsal': i + 1, 'posicion': 'CENTRAL', 'fecha_nacimiento': '2005-01-01',
            })
        # La tabla de equipos sigue intacta: ninguna sentencia inyectada se ejecutó.
        self.assertTrue(Equipo.objects.filter(pk=equipo.pk).exists())
        self.assertEqual(Jugadora.objects.filter(equipo=equipo).count(), len(self.PAYLOADS_SQLI))
        for payload in self.PAYLOADS_SQLI:
            self.assertTrue(Jugadora.objects.filter(equipo=equipo, nombre=payload).exists())

    def test_accion_con_script_inyectado_en_json_es_rechazada_con_400(self):
        _, equipo, jugadora, partido = _crear_entrenador_con_partido('coach_xss_api')
        self.client.login(username='coach_xss_api', password='pass12345')
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': partido.id,
                'jugadora_id': jugadora.id,
                'accion': "<script>alert(1)</script>",
                'calidad': '++',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RegistroEstadistica.objects.filter(partido=partido).count(), 0)

    def test_body_json_corrupto_no_provoca_500(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data="{esto-no-es-json-valido::",
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_body_json_con_array_en_vez_de_objeto_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps([1, 2, 3]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)


class ValidacionEstrictaDeTiposTests(TestCase):
    """OWASP A04:2021 - Insecure Design. Todo ID numérico que llega en un
    payload JSON debe ser estrictamente un entero: nunca una lista, un
    objeto, un booleano o un string con caracteres de inyección."""

    def setUp(self):
        cache.clear()
        _, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido('coach_tipos')
        self.client.login(username='coach_tipos', password='pass12345')

    def test_partido_id_como_lista_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({'partido_id': [1, 2], 'accion': 'ATAQUE', 'calidad': '++'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_partido_id_como_objeto_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({'partido_id': {'$ne': None}, 'accion': 'ATAQUE'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_partido_id_con_texto_de_inyeccion_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({'partido_id': "1 OR 1=1", 'accion': 'ATAQUE'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_partido_id_booleano_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({'partido_id': True, 'accion': 'ATAQUE'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_id_registro_a_eliminar_no_entero_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_eliminar_estadistica'),
            data=json.dumps({'id': "'; DROP TABLE stats_app_registroestadistica; --"}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_calidad_fuera_de_catalogo_es_rechazada(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id, 'jugadora_id': self.jugadora.id,
                'accion': 'ATAQUE', 'calidad': 'PUNTAZO_INVENTADO',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_payload_valido_es_aceptado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id, 'jugadora_id': self.jugadora.id,
                'accion': 'ATAQUE', 'calidad': '++', 'fase': 'K1', 'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    # ── Los formularios de validación también se testean de forma unitaria ──
    def test_form_registrar_accion_rechaza_id_no_entero(self):
        form = RegistrarAccionForm({'partido_id': 'DROP TABLE', 'accion': 'ATAQUE'})
        self.assertFalse(form.is_valid())

    def test_form_registrar_accion_rechaza_accion_fuera_de_catalogo(self):
        form = RegistrarAccionForm({'partido_id': 1, 'accion': "<script>evil()</script>"})
        self.assertFalse(form.is_valid())

    def test_form_registrar_accion_acepta_payload_valido(self):
        form = RegistrarAccionForm({
            'partido_id': 1, 'jugadora_id': 2, 'accion': 'ATAQUE',
            'calidad': '++', 'fase': 'K1', 'set_numero': 1, 'rotacion_num': 3,
        })
        self.assertTrue(form.is_valid())

    def test_form_registrar_cambio_exige_los_tres_ids(self):
        form = RegistrarCambioForm({'partido_id': 1, 'sale_id': 2})
        self.assertFalse(form.is_valid())
        self.assertIn('entra_id', form.errors)

    def test_form_eliminar_accion_rechaza_lista(self):
        form = EliminarAccionForm({'id': [1]})
        self.assertFalse(form.is_valid())


class IDORTests(TestCase):
    """OWASP A01:2021 - Broken Access Control (Insecure Direct Object
    Reference). Cada API de scouting/rotaciones debe responder 404 —nunca
    200, nunca un 403 diferenciado— cuando el `partido_id`/`jugadora_id`/
    `id` referenciado pertenece a OTRO entrenador. Un 404 uniforme evita
    filtrar siquiera si el identificador existe en el sistema."""

    def setUp(self):
        cache.clear()
        self.coach_a = User.objects.create_user(username='idor_a', password='pass12345')
        _, self.equipo_b, self.jugadora_b1, self.partido_b = _crear_entrenador_con_partido('idor_b')
        self.jugadora_b2 = Jugadora.objects.create(
            equipo=self.equipo_b, nombre='Eva', apellidos='Q', dorsal=2, posicion='OPUESTA'
        )
        self.registro_b = RegistroEstadistica.objects.create(
            partido=self.partido_b, jugadora=self.jugadora_b1, tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=1,
        )
        self.nota_b = NotaPartido.objects.create(
            partido=self.partido_b, set_numero=1, texto='Nota ajena',
        )
        RotacionSet.objects.create(
            partido=self.partido_b, set_numero=1, es_inicial=True, pos1=self.jugadora_b1
        )
        self.client.login(username='idor_a', password='pass12345')

    def test_registrar_cambio_sobre_partido_ajeno_da_404(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_cambio'),
            data=json.dumps({
                'partido_id': self.partido_b.id,
                'sale_id': self.jugadora_b1.id,
                'entra_id': self.jugadora_b2.id,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_eliminar_registro_ajeno_da_404_y_no_lo_borra(self):
        response = self.client.post(
            reverse('stats_app:api_eliminar_estadistica'),
            data=json.dumps({'id': self.registro_b.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(RegistroEstadistica.objects.filter(pk=self.registro_b.id).exists())

    def test_config_set_de_partido_ajeno_da_404_y_no_lo_modifica(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_config_set', args=[self.partido_b.id]),
            data=json.dumps({'puntos_por_set': 30, 'puntos_set_decisivo': 20, 'sets_para_ganar': 5}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.partido_b.refresh_from_db()
        self.assertEqual(self.partido_b.puntos_por_set, 25)

    def test_obtener_rotacion_de_partido_ajeno_da_404(self):
        response = self.client.get(reverse('stats_app:api_get_rotacion', args=[self.partido_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_guardar_alineacion_de_partido_ajeno_da_404(self):
        response = self.client.post(
            reverse('stats_app:api_guardar_rotacion_inicial', args=[self.partido_b.id]),
            data=json.dumps({'pos1': self.jugadora_b1.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_rotar_manualmente_partido_ajeno_da_404(self):
        response = self.client.post(
            reverse('stats_app:api_rotar_manual', args=[self.partido_b.id]),
            data=json.dumps({'direccion': 'horario'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_listar_plantillas_de_partido_ajeno_da_404(self):
        response = self.client.get(reverse('stats_app:api_plantillas_rotacion', args=[self.partido_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_guardar_plantilla_de_partido_ajeno_da_404(self):
        response = self.client.post(
            reverse('stats_app:api_guardar_plantilla_rotacion', args=[self.partido_b.id]),
            data=json.dumps({'clave': 'TITULAR', 'pos1': self.jugadora_b1.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(LineupPreset.objects.filter(equipo=self.equipo_b).exists())

    def test_actualizar_posicion_de_jugadora_ajena_da_404_y_no_la_modifica(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_pos_jugadora'),
            data=json.dumps({'jugadora_id': self.jugadora_b1.id, 'posicion': 'LIBERO'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.jugadora_b1.refresh_from_db()
        self.assertEqual(self.jugadora_b1.posicion, 'OPUESTA')

    def test_finalizar_partido_ajeno_da_404_y_no_lo_finaliza(self):
        response = self.client.post(reverse('stats_app:api_finalizar_partido', args=[self.partido_b.id]))
        self.assertEqual(response.status_code, 404)
        self.partido_b.refresh_from_db()
        self.assertFalse(self.partido_b.finalizado)

    def test_reabrir_partido_ajeno_da_404(self):
        self.partido_b.finalizado = True
        self.partido_b.save(update_fields=['finalizado'])
        response = self.client.post(reverse('stats_app:api_reabrir_partido', args=[self.partido_b.id]))
        self.assertEqual(response.status_code, 404)
        self.partido_b.refresh_from_db()
        self.assertTrue(self.partido_b.finalizado)

    def test_historial_set_de_partido_ajeno_da_404(self):
        response = self.client.get(
            reverse('stats_app:api_historial_set', args=[self.partido_b.id]),
            {'set': 1},
        )
        self.assertEqual(response.status_code, 404)

    def test_listar_notas_de_partido_ajeno_da_404(self):
        response = self.client.get(reverse('stats_app:api_list_notas', args=[self.partido_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_crear_nota_en_partido_ajeno_da_404(self):
        response = self.client.post(
            reverse('stats_app:api_crear_nota', args=[self.partido_b.id]),
            data=json.dumps({'texto': 'Intento IDOR', 'set_numero': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            NotaPartido.objects.filter(partido=self.partido_b, texto='Intento IDOR').exists()
        )

    def test_eliminar_nota_ajena_da_404(self):
        response = self.client.post(
            reverse('stats_app:api_eliminar_nota', args=[self.partido_b.id, self.nota_b.id]),
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(NotaPartido.objects.filter(pk=self.nota_b.id).exists())

    def test_obtener_stats_set_de_partido_propio_si_funciona(self):
        """Control positivo: el mismo tipo de petición SÍ debe funcionar
        sobre un recurso propio, para no confundir aislamiento con un bug."""
        _, _, jugadora_a, partido_a = _crear_entrenador_con_partido('idor_a_control')
        # Reusa la sesión ya logueada de coach_a creando el partido bajo su propio equipo.
        equipo_a = Equipo.objects.filter(entrenador=self.coach_a).first()
        if not equipo_a:
            equipo_a = Equipo.objects.create(
                entrenador=self.coach_a, nombre='Equipo A', temporada='2025/2026', categoria='SENIOR'
            )
        partido_propio = Partido.objects.create(
            equipo=equipo_a, fecha=date(2026, 2, 2), hora=time(18, 0),
            rival='Propio', local=True, lugar='Casa',
        )
        response = self.client.post(
            reverse('stats_app:api_obtener_stats_set'),
            data=json.dumps({'partido_id': partido_propio.id, 'set_numero': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)


class FuerzaBrutaYRateLimitTests(TestCase):
    """OWASP A07:2021 - Identification & Authentication Failures.
    Verifica de extremo a extremo (vía self.client, con el middleware real
    instalado en settings.MIDDLEWARE) que un script de fuerza bruta contra
    el login es bloqueado con 429 tras superar el umbral configurado."""

    def setUp(self):
        cache.clear()
        User.objects.create_user(username='victima', password='ContraseñaCorrecta123!')

    def tearDown(self):
        cache.clear()

    def _limite_login(self):
        for pattern, limit, window in settings.RATE_LIMIT_RULES:
            if 'login' in pattern:
                return limit
        self.fail('No hay regla de rate limit configurada para /accounts/login/')

    def test_bloquea_fuerza_bruta_contra_login_tras_superar_el_limite(self):
        login_url = reverse('login')
        limite = self._limite_login()
        codigos = []
        for i in range(limite + 5):
            respuesta = self.client.post(login_url, {'username': 'victima', 'password': f'intento_incorrecto_{i}'})
            codigos.append(respuesta.status_code)

        self.assertIn(429, codigos, "El middleware debe devolver 429 tras superar el límite de intentos.")
        primer_429 = codigos.index(429)
        # Antes del límite, el login "funciona" (200 con error de credenciales); nunca autentica al atacante.
        self.assertTrue(all(c == 200 for c in codigos[:primer_429]))

    def test_credenciales_correctas_bajo_el_limite_si_autentican(self):
        login_url = reverse('login')
        respuesta = self.client.post(login_url, {'username': 'victima', 'password': 'ContraseñaCorrecta123!'})
        self.assertEqual(respuesta.status_code, 302)

    def test_bloqueo_de_login_no_afecta_a_otra_ip(self):
        login_url = reverse('login')
        limite = self._limite_login()
        for i in range(limite + 3):
            self.client.post(
                login_url, {'username': 'victima', 'password': f'x{i}'},
                REMOTE_ADDR='10.0.0.5',
            )
        respuesta_otra_ip = self.client.post(
            login_url, {'username': 'victima', 'password': 'ContraseñaCorrecta123!'},
            REMOTE_ADDR='10.0.0.99',
        )
        self.assertEqual(respuesta_otra_ip.status_code, 302)


class RateLimitMiddlewareUnitTests(TestCase):
    """Tests unitarios del middleware, aislados del resto de la pila HTTP:
    permiten fijar límites artificialmente bajos para probar el
    comportamiento exacto de bloqueo/desbloqueo sin depender de vistas."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _handler(request):
        return HttpResponse('ok')

    def _middleware(self, rules):
        with override_settings(RATE_LIMIT_RULES=rules):
            return RateLimitMiddleware(get_response=self._handler)

    def test_permite_hasta_el_limite_y_bloquea_a_partir_de_ahi(self):
        mw = self._middleware([(r'^/api/', 3, 60)])
        request = self.factory.get('/api/algo/')
        codigos = [mw(request).status_code for _ in range(5)]
        self.assertEqual(codigos, [200, 200, 200, 429, 429])

    def test_respuesta_429_incluye_retry_after(self):
        mw = self._middleware([(r'^/api/', 1, 30)])
        request = self.factory.get('/api/algo/')
        mw(request)
        bloqueada = mw(request)
        self.assertEqual(bloqueada.status_code, 429)
        self.assertIn('Retry-After', bloqueada)

    def test_ips_distintas_tienen_contadores_independientes(self):
        mw = self._middleware([(r'^/api/', 1, 60)])
        r1 = self.factory.get('/api/algo/', REMOTE_ADDR='1.1.1.1')
        r2 = self.factory.get('/api/algo/', REMOTE_ADDR='2.2.2.2')
        self.assertEqual(mw(r1).status_code, 200)
        self.assertEqual(mw(r2).status_code, 200)

    def test_respeta_x_forwarded_for_para_identificar_al_cliente_real_tras_el_proxy(self):
        mw = self._middleware([(r'^/api/', 1, 60)])
        r1 = self.factory.get('/api/algo/', HTTP_X_FORWARDED_FOR='9.9.9.9, 10.0.0.1')
        r2 = self.factory.get('/api/algo/', HTTP_X_FORWARDED_FOR='9.9.9.9, 10.0.0.2')
        self.assertEqual(mw(r1).status_code, 200)
        # Misma IP real (9.9.9.9) detrás de dos proxies internos distintos → mismo contador.
        self.assertEqual(mw(r2).status_code, 429)

    def test_rutas_fuera_de_las_reglas_nunca_se_limitan(self):
        mw = self._middleware([(r'^/api/', 1, 60)])
        request = self.factory.get('/dashboard-no-limitado/')
        codigos = [mw(request).status_code for _ in range(10)]
        self.assertTrue(all(c == 200 for c in codigos))


class ConcurrenciaYRaceConditionsTests(TransactionTestCase):
    """Simula dos acciones de scouting (o dos rotaciones) llegando
    prácticamente en el mismo instante —dos dispositivos en el banquillo,
    un doble-tap, o un reintento automático del frontend solapándose con
    la petición original— y comprueba que ninguna se pierde.

    Usa TransactionTestCase (en vez de TestCase) porque los hilos necesitan
    confirmar sus propias transacciones de verdad; TestCase envuelve cada
    test en una única transacción que no es segura entre hilos.
    """

    def setUp(self):
        cache.clear()
        self.coach = User.objects.create_user(username='concurrencia_coach', password='pass12345')
        self.equipo = Equipo.objects.create(
            entrenador=self.coach, nombre='Concurrencia FC', temporada='2025/2026', categoria='SENIOR'
        )
        self.jugadora = Jugadora.objects.create(
            equipo=self.equipo, nombre='Val', apellidos='Con', dorsal=7, posicion='OPUESTA'
        )
        self.partido = Partido.objects.create(
            equipo=self.equipo, fecha=date(2026, 2, 1), hora=time(18, 0),
            rival='Rival Concurrente', local=True, lugar='Pabellón',
        )

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _con_reintento_por_bloqueo_sqlite(accion, intentos=15):
        """SQLite (motor usado en tests) serializa TODAS las escrituras a
        nivel de conexión/tabla, algo que Postgres/Neon en producción no
        sufre gracias a MVCC (lecturas y escrituras concurrentes reales).
        Bajo hilos reales, dos escrituras casi simultáneas en SQLite pueden
        chocar con "database/table is locked" antes de que le toque el turno
        a la segunda. Esto es una limitación del motor de test, no de la
        aplicación: se reintenta aquí para poder ejercitar la concurrencia
        real de Django/hilos sin que el test sea inestable por culpa de
        SQLite. Cualquier otro tipo de error se propaga tal cual.
        """
        def es_bloqueo_transitorio(exc):
            textos = [str(e) for e in (exc, exc.__cause__, exc.__context__) if e is not None]
            if any('locked' in t.lower() for t in textos):
                return True
            # `django.contrib.sessions.exceptions.UpdateError` no siempre
            # conserva el mensaje del OperationalError original al guardar
            # la sesión bajo contención; en este test solo puede deberse a
            # la misma limitación de escritura concurrente de SQLite.
            return type(exc).__name__ == 'UpdateError'

        ultimo_error = None
        for intento in range(intentos):
            try:
                return accion()
            except Exception as exc:
                ultimo_error = exc
                if not es_bloqueo_transitorio(exc):
                    raise
                threading.Event().wait(0.05 * (intento + 1))
        raise ultimo_error

    def _cliente_autenticado(self):
        client = Client()
        self._con_reintento_por_bloqueo_sqlite(
            lambda: client.login(username='concurrencia_coach', password='pass12345')
        )
        return client

    def test_dos_acciones_de_scouting_simultaneas_no_pierden_ninguna(self):
        """El marcador se recalcula siempre con COUNT() sobre las filas
        reales (nunca con un contador mutable en memoria), así que el test
        clave es: ¿sobreviven AMBOS INSERTs concurrentes?"""
        resultados = {}

        def registrar_accion(indice):
            try:
                # El reintento por bloqueo SOLO se aplica al login (creación
                # de sesión), nunca a la petición de escritura en sí: esta
                # vista ya tiene su propio `@reintentar_en_error_transitorio`
                # (stats_app.db_utils) para eso. Reintentar aquí también el
                # POST duplicaría artificialmente el registro si el bloqueo
                # ocurriera justo después del commit, distorsionando la
                # aserción de abajo con un problema del arnés de test, no de
                # la aplicación.
                client = self._cliente_autenticado()
                response = client.post(
                    reverse('stats_app:api_registrar_estadistica'),
                    data=json.dumps({
                        'partido_id': self.partido.id,
                        'jugadora_id': self.jugadora.id,
                        'fase': 'K1',
                        'accion': 'ATAQUE',
                        'calidad': '++',
                        'set_numero': 1,
                    }),
                    content_type='application/json',
                )
                resultados[indice] = response.status_code
            except Exception as exc:
                resultados[indice] = f'EXCEPTION: {exc!r}'

        hilos = [threading.Thread(target=registrar_accion, args=(i,)) for i in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=20)

        self.assertEqual(resultados.get(0), 200, resultados)
        self.assertEqual(resultados.get(1), 200, resultados)

        total = RegistroEstadistica.objects.filter(
            partido=self.partido, set_numero=1, accion='ATAQUE'
        ).count()
        # La propiedad de seguridad que nos interesa es "ninguna se pierde"
        # (total >= 2, nunca 0 ni 1). SQLite (solo en tests) puede, bajo
        # contención real de hilos, forzar un reintento interno legítimo de
        # `@reintentar_en_error_transitorio` si el bloqueo ocurre tras el
        # commit; eso es el trade-off ya documentado en db_utils.py (posible
        # duplicado corregible con "eliminar", nunca pérdida de datos) y no
        # ocurre en Postgres/Neon, cuyo MVCC no serializa así los INSERTs.
        self.assertGreaterEqual(
            total, 2,
            "Las dos acciones concurrentes deben persistir ambas; el marcador "
            "no puede perder un punto por una condición de carrera.",
        )

    def test_dos_rotaciones_manuales_simultaneas_no_lanzan_excepciones_ni_se_pierden(self):
        RotacionSet.objects.create(
            partido=self.partido, set_numero=1, es_inicial=True,
            pos1=self.jugadora, pos2=self.jugadora, pos3=self.jugadora,
            pos4=self.jugadora, pos5=self.jugadora, pos6=self.jugadora,
        )
        resultados = {}

        def rotar(indice):
            try:
                client = self._cliente_autenticado()
                response = client.post(
                    reverse('stats_app:api_rotar_manual', args=[self.partido.id]),
                    data=json.dumps({'set_numero': 1, 'direccion': 'horario'}),
                    content_type='application/json',
                )
                resultados[indice] = response.status_code
            except Exception as exc:
                resultados[indice] = f'EXCEPTION: {exc!r}'

        hilos = [threading.Thread(target=rotar, args=(i,)) for i in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=20)

        self.assertEqual(resultados.get(0), 200, resultados)
        self.assertEqual(resultados.get(1), 200, resultados)
        # 1 inicial + 2 rotaciones generadas >= 3 filas; ninguna petición se
        # pierde (ver comentario equivalente más arriba sobre reintentos
        # internos sobre SQLite sin contrapartida en Postgres/Neon).
        self.assertGreaterEqual(
            RotacionSet.objects.filter(partido=self.partido, set_numero=1).count(), 3
        )


class ResilienciaBaseDeDatosTests(TestCase):
    """Verifica el decorador `reintentar_en_error_transitorio` (stats_app.db_utils)
    que complementa CONN_HEALTH_CHECKS ante micro-cortes de red con Neon:
    reintenta automáticamente errores de conexión, pero nunca oculta un
    error de lógica/negocio."""

    def test_reintenta_ante_error_transitorio_y_finalmente_tiene_exito(self):
        llamadas = {'n': 0}

        @reintentar_en_error_transitorio(max_intentos=3, backoff_base=0)
        def vista_con_micro_corte():
            llamadas['n'] += 1
            if llamadas['n'] < 2:
                raise OperationalError('simulated: server closed the connection unexpectedly')
            return 'ok'

        resultado = vista_con_micro_corte()
        self.assertEqual(resultado, 'ok')
        self.assertEqual(llamadas['n'], 2)

    def test_agota_reintentos_y_propaga_el_error_si_la_bd_no_vuelve(self):
        @reintentar_en_error_transitorio(max_intentos=2, backoff_base=0)
        def vista_bd_caida():
            raise OperationalError('conexión perdida permanentemente')

        with self.assertRaises(OperationalError):
            vista_bd_caida()

    def test_no_reintenta_errores_de_logica_ajenos_a_la_conexion(self):
        llamadas = {'n': 0}

        @reintentar_en_error_transitorio(max_intentos=3, backoff_base=0)
        def vista_con_bug():
            llamadas['n'] += 1
            raise ValueError('esto es un bug de negocio, no un corte de red')

        with self.assertRaises(ValueError):
            vista_con_bug()
        # Un error de lógica no debe reintentarse: solo se llama una vez.
        self.assertEqual(llamadas['n'], 1)

    def test_endpoint_real_de_scouting_sigue_respondiendo_ok_en_condiciones_normales(self):
        """Test de humo: el decorador no interfiere con el flujo normal
        (sin errores) de la API más usada durante un partido en vivo."""
        _, equipo, jugadora, partido = _crear_entrenador_con_partido('coach_resiliencia')
        self.client.login(username='coach_resiliencia', password='pass12345')
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': partido.id, 'jugadora_id': jugadora.id,
                'accion': 'SAQUE', 'calidad': '++', 'fase': 'K0', 'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)


# ═════════════════════════════════════════════════════════════════════════════
# COBERTURA FUNCIONAL — flujos "felices" completos
#
# La suite de intrusión de arriba se centra en el camino del atacante; estos
# tests recorren el camino normal (con datos reales de un partido) de las
# vistas más grandes —donde vive la lógica de negocio— para que el
# blindaje de seguridad no quede sin ejercitar el resto del comportamiento.
# ═════════════════════════════════════════════════════════════════════════════

class FlujoCompletoPartidoTests(TestCase):
    """Recorre en caliente las APIs de scouting/rotaciones/informes con datos
    realistas de un partido en curso: alineación, varias acciones de varios
    fundamentos y calidades, un cambio, una rotación y el cierre del set."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, _, self.partido = _crear_entrenador_con_partido('coach_flujo')
        self.jugadoras = [
            Jugadora.objects.create(
                equipo=self.equipo, nombre=f'Jugadora{i}', apellidos='Test',
                dorsal=i, posicion='CENTRAL',
            )
            for i in range(1, 8)
        ]
        self.client.login(username='coach_flujo', password='pass12345')

        # Alineación inicial completa (6 posiciones + libero) vía el modelo
        # directamente, para centrar las peticiones HTTP en lo que se testea.
        RotacionSet.objects.create(
            partido=self.partido, set_numero=1, es_inicial=True,
            pos1=self.jugadoras[0], pos2=self.jugadoras[1], pos3=self.jugadoras[2],
            pos4=self.jugadoras[3], pos5=self.jugadoras[4], pos6=self.jugadoras[5],
            libero1=self.jugadoras[6],
        )

        # Varias acciones con distintos fundamentos/calidades para ejercitar
        # los cálculos de eficacia, líderes, destacados y K1/K2 de reporting.py.
        acciones = [
            ('SAQUE', '++', self.jugadoras[0]), ('SAQUE', '--', self.jugadoras[0]),
            ('RECEPCION', '++', self.jugadoras[1]), ('RECEPCION', '-', self.jugadoras[1]),
            ('ATAQUE', '++', self.jugadoras[2]), ('ATAQUE', '--', self.jugadoras[2]),
            ('BLOQUEO', '++', self.jugadoras[3]), ('DEFENSA', '+', self.jugadoras[4]),
            ('ERROR_RIVAL', None, None), ('PUNTO_RIVAL', None, None),
        ]
        for accion, calidad, jugadora in acciones:
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=jugadora, tipo_fase='K1',
                accion=accion, calidad=calidad or '', set_numero=1,
            )

    def test_modo_partido_con_historial_renderiza_ok(self):
        response = self.client.get(reverse('stats_app:modo_partido', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 200)

    def test_obtener_stats_set_con_datos_reales_devuelve_metricas_completas(self):
        response = self.client.post(
            reverse('stats_app:api_obtener_stats_set'),
            data=json.dumps({'partido_id': self.partido.id, 'set_numero': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertIn('lideres', data)
        self.assertIn('destacados_por_accion', data)
        self.assertIn('sets_con_datos', data)
        self.assertIn('rotaciones', data)
        self.assertGreaterEqual(data['puntos_local'], 1)
        self.assertGreaterEqual(data['puntos_rival'], 1)

    def test_get_stats_json_devuelve_seguimiento_y_alertas(self):
        response = self.client.get(
            reverse('stats_app:api_get_stats_json', args=[self.partido.id, 1])
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertIn('alertas_cambio', data)

    def test_partido_stats_final_global_y_por_set(self):
        response_global = self.client.get(reverse('stats_app:partido_stats_final', args=[self.partido.pk]))
        self.assertEqual(response_global.status_code, 200)

        response_set = self.client.get(
            reverse('stats_app:partido_stats_final', args=[self.partido.pk]), {'set': '1'}
        )
        self.assertEqual(response_set.status_code, 200)

    def test_descargar_resumen_pdf_genera_documento(self):
        response = self.client.get(reverse('stats_app:descargar_resumen_pdf', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_descargar_informe_completo_pdf_genera_documento(self):
        response = self.client.get(reverse('stats_app:descargar_informe_completo', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_informe_completo_pdf_partido_finalizado_se_cachea_en_bd(self):
        """Primera descarga tras finalizar genera y guarda el PDF; la
        segunda debe servirse de la caché sin volver a invocar xhtml2pdf."""
        self.partido.finalizado = True
        self.partido.save()
        url = reverse('stats_app:descargar_informe_completo', args=[self.partido.pk])

        from .views import informes as informes_module
        with patch.object(
            informes_module, 'render_to_pdf', wraps=informes_module.render_to_pdf
        ) as spy_render:
            resp1 = self.client.get(url)
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(spy_render.call_count, 1)

            self.partido.refresh_from_db()
            self.assertIsNotNone(self.partido.informe_pdf_cache)
            self.assertEqual(
                self.partido.informe_pdf_cache_num_registros,
                RegistroEstadistica.objects.filter(partido=self.partido).count(),
            )

            resp2 = self.client.get(url)
            self.assertEqual(resp2.status_code, 200)
            # La segunda petición no debe volver a renderizar: sigue en 1.
            self.assertEqual(spy_render.call_count, 1)
            self.assertEqual(resp1.content, resp2.content)

    def test_informe_completo_pdf_cache_se_invalida_si_cambian_los_datos(self):
        """Añadir una acción tras finalizar (p.ej. una corrección) debe
        invalidar la caché y regenerar el PDF con los datos actualizados."""
        self.partido.finalizado = True
        self.partido.save()
        url = reverse('stats_app:descargar_informe_completo', args=[self.partido.pk])

        from .views import informes as informes_module
        with patch.object(
            informes_module, 'render_to_pdf', wraps=informes_module.render_to_pdf
        ) as spy_render:
            self.client.get(url)
            self.assertEqual(spy_render.call_count, 1)

            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=self.jugadoras[0], tipo_fase='K1',
                accion='ATAQUE', calidad='++', set_numero=1,
            )
            self.client.get(url)
            self.assertEqual(spy_render.call_count, 2)

            self.partido.refresh_from_db()
            self.assertEqual(
                self.partido.informe_pdf_cache_num_registros,
                RegistroEstadistica.objects.filter(partido=self.partido).count(),
            )

    def test_informe_completo_pdf_no_cachea_si_partido_no_finalizado(self):
        url = reverse('stats_app:descargar_informe_completo', args=[self.partido.pk])
        self.client.get(url)
        self.partido.refresh_from_db()
        self.assertIsNone(self.partido.informe_pdf_cache)

    def test_informe_completo_pdf_por_set_no_usa_ni_llena_la_cache(self):
        self.partido.finalizado = True
        self.partido.save()
        url = reverse('stats_app:descargar_informe_completo', args=[self.partido.pk])
        response = self.client.get(url, {'set': '1'})
        self.assertEqual(response.status_code, 200)
        self.partido.refresh_from_db()
        self.assertIsNone(self.partido.informe_pdf_cache)

    def test_partido_stats_avanzado_view_renderiza(self):
        response = self.client.get(reverse('stats_app:partido_stats_avanzado', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Estadísticas Avanzadas')
        self.assertContains(response, 'Escala completa')

    def test_descargar_informe_avanzado_pdf_genera_documento(self):
        response = self.client.get(reverse('stats_app:descargar_informe_avanzado', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(b'PDF', response.content[:10])

    def test_descargar_manual_usuario_pdf_genera_documento(self):
        response = self.client.get(reverse('stats_app:descargar_manual_usuario'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(b'PDF', response.content[:10])

    def test_registrar_cambio_happy_path(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_cambio'),
            data=json.dumps({
                'partido_id': self.partido.id,
                'sale_id': self.jugadoras[0].id,
                'entra_id': self.jugadoras[6].id,
                'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_eliminar_accion_propia_happy_path(self):
        registro = RegistroEstadistica.objects.filter(partido=self.partido).first()
        response = self.client.post(
            reverse('stats_app:api_eliminar_estadistica'),
            data=json.dumps({'id': registro.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RegistroEstadistica.objects.filter(pk=registro.id).exists())

    def test_actualizar_config_set_happy_path(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_config_set', args=[self.partido.id]),
            data=json.dumps({'puntos_por_set': 21, 'puntos_set_decisivo': 15, 'sets_para_ganar': 3}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.puntos_por_set, 21)

    def test_actualizar_config_set_rechaza_valores_invalidos(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_config_set', args=[self.partido.id]),
            data=json.dumps({'puntos_por_set': 0, 'puntos_set_decisivo': 15, 'sets_para_ganar': 3}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_get_rotacion_actual_devuelve_alineacion_inicial_si_no_hay_actual(self):
        response = self.client.get(reverse('stats_app:api_get_rotacion', args=[self.partido.id]), {'set': 1})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['pos1']['dorsal'], self.jugadoras[0].dorsal)

    def test_get_rotacion_actual_404_si_no_existe_ninguna(self):
        response = self.client.get(reverse('stats_app:api_get_rotacion', args=[self.partido.id]), {'set': 2})
        self.assertEqual(response.status_code, 404)

    def test_guardar_alineacion_inicial_happy_path_marca_tambien_actual(self):
        response = self.client.post(
            reverse('stats_app:api_guardar_rotacion_inicial', args=[self.partido.id]),
            data=json.dumps({
                'set_numero': 2,
                'pos1': self.jugadoras[0].id, 'pos2': self.jugadoras[1].id,
                'pos3': self.jugadoras[2].id, 'pos4': self.jugadoras[3].id,
                'pos5': self.jugadoras[4].id, 'pos6': self.jugadoras[5].id,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RotacionSet.objects.filter(partido=self.partido, set_numero=2).count(), 2
        )  # una es_inicial=True y otra es_inicial=False

    def test_guardar_alineacion_solo_actual_no_toca_la_inicial(self):
        response = self.client.post(
            reverse('stats_app:api_guardar_rotacion_inicial', args=[self.partido.id]),
            data=json.dumps({
                'set_numero': 1, 'solo_actual': True,
                'pos1': self.jugadoras[6].id, 'pos2': self.jugadoras[1].id,
                'pos3': self.jugadoras[2].id, 'pos4': self.jugadoras[3].id,
                'pos5': self.jugadoras[4].id, 'pos6': self.jugadoras[5].id,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        inicial = RotacionSet.objects.get(partido=self.partido, set_numero=1, es_inicial=True)
        self.assertEqual(inicial.pos1_id, self.jugadoras[0].id)  # sin cambios

    def test_guardar_alineacion_con_jugadora_de_otro_equipo_es_rechazada(self):
        _, _, jugadora_ajena, _ = _crear_entrenador_con_partido('coach_flujo_intruso')
        response = self.client.post(
            reverse('stats_app:api_guardar_rotacion_inicial', args=[self.partido.id]),
            data=json.dumps({
                'set_numero': 3,
                'pos1': jugadora_ajena.id, 'pos2': self.jugadoras[1].id,
                'pos3': self.jugadoras[2].id, 'pos4': self.jugadoras[3].id,
                'pos5': self.jugadoras[4].id, 'pos6': self.jugadoras[5].id,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_rotar_manual_horario_y_antihorario(self):
        r1 = self.client.post(
            reverse('stats_app:api_rotar_manual', args=[self.partido.id]),
            data=json.dumps({'set_numero': 1, 'direccion': 'horario'}),
            content_type='application/json',
        )
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(
            reverse('stats_app:api_rotar_manual', args=[self.partido.id]),
            data=json.dumps({'set_numero': 1, 'direccion': 'antihorario'}),
            content_type='application/json',
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(
            RotacionSet.objects.filter(partido=self.partido, set_numero=1).count(), 3
        )

    def test_rotar_manual_minivoley(self):
        equipo_mini = Equipo.objects.create(
            entrenador=self.coach, nombre='Mini FC', temporada='2025/2026', categoria='BENJAMIN'
        )
        jugs = [
            Jugadora.objects.create(equipo=equipo_mini, nombre=f'M{i}', apellidos='T', dorsal=i, posicion='CENTRAL')
            for i in range(1, 5)
        ]
        partido_mini = Partido.objects.create(
            equipo=equipo_mini, fecha=date(2026, 4, 1), hora=time(17, 0),
            rival='Rival Mini', local=True, lugar='Sala', modalidad='MINIVOLEY',
        )
        RotacionSet.objects.create(
            partido=partido_mini, set_numero=1, es_inicial=True,
            pos1=jugs[0], pos2=jugs[1], pos3=jugs[2], pos4=jugs[3],
        )
        response = self.client.post(
            reverse('stats_app:api_rotar_manual', args=[partido_mini.id]),
            data=json.dumps({'set_numero': 1, 'direccion': 'horario'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_actualizar_posicion_jugadora_happy_path(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_pos_jugadora'),
            data=json.dumps({'jugadora_id': self.jugadoras[0].id, 'posicion': 'LIBERO'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.jugadoras[0].refresh_from_db()
        self.assertEqual(self.jugadoras[0].posicion, 'LIBERO')

    def test_finalizar_partido_happy_path(self):
        response = self.client.post(reverse('stats_app:api_finalizar_partido', args=[self.partido.id]))
        self.assertEqual(response.status_code, 200)
        self.partido.refresh_from_db()
        self.assertTrue(self.partido.finalizado)

    def test_reabrir_partido_happy_path_conserva_stats(self):
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadoras[0], tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=3,
        )
        self.partido.finalizado = True
        self.partido.save(update_fields=['finalizado'])
        response = self.client.post(reverse('stats_app:api_reabrir_partido', args=[self.partido.id]))
        self.assertEqual(response.status_code, 200)
        self.partido.refresh_from_db()
        self.assertFalse(self.partido.finalizado)
        self.assertEqual(
            RegistroEstadistica.objects.filter(partido=self.partido, set_numero=3).count(),
            1,
        )

    def test_historial_set_devuelve_acciones_del_set_pedido(self):
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadoras[0], tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadoras[1], tipo_fase='K1',
            accion='RECEPCION', calidad='+', set_numero=3,
        )
        response = self.client.get(
            reverse('stats_app:api_historial_set', args=[self.partido.id]),
            {'set': 3},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['set_numero'], 3)
        self.assertEqual(len(data['historial']), 1)
        self.assertIn('Recepción', data['historial'][0]['accion_texto'])

    def test_informe_final_incluye_zona_rotacion_y_racha(self):
        """El informe final (web y PDF) debe incluir los nuevos bloques de
        rendimiento por zona, eficacia por rotación y racha máxima, que se
        calculan a partir del histórico completo del set."""
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadoras[2], tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=1, zona=4, rotacion_num=1,
        )

        response = self.client.get(reverse('stats_app:partido_stats_final', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Rendimiento por Zona', content)
        self.assertIn('Eficacia por Rotación', content)
        self.assertIn('Zona 4', content)

        pdf_response = self.client.get(reverse('stats_app:descargar_informe_completo', args=[self.partido.pk]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response['Content-Type'], 'application/pdf')


class LineupPresetTests(TestCase):
    """Guardar/cargar Titular y B, último set del equipo, aislamiento."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, _, self.partido = _crear_entrenador_con_partido('coach_preset')
        self.jugadoras = [
            Jugadora.objects.create(
                equipo=self.equipo, nombre=f'P{i}', apellidos='Test',
                dorsal=i, posicion='CENTRAL',
            )
            for i in range(1, 8)
        ]
        self.client.login(username='coach_preset', password='pass12345')
        self.payload_titular = {
            'clave': 'TITULAR',
            'pos1': self.jugadoras[0].id, 'pos2': self.jugadoras[1].id,
            'pos3': self.jugadoras[2].id, 'pos4': self.jugadoras[3].id,
            'pos5': self.jugadoras[4].id, 'pos6': self.jugadoras[5].id,
            'libero1': self.jugadoras[6].id,
        }

    def test_guardar_titular_y_listar(self):
        guardar = self.client.post(
            reverse('stats_app:api_guardar_plantilla_rotacion', args=[self.partido.id]),
            data=json.dumps(self.payload_titular),
            content_type='application/json',
        )
        self.assertEqual(guardar.status_code, 200)
        self.assertEqual(LineupPreset.objects.filter(equipo=self.equipo, clave='TITULAR').count(), 1)

        listado = self.client.get(
            reverse('stats_app:api_plantillas_rotacion', args=[self.partido.id]),
            {'set': 1},
        )
        self.assertEqual(listado.status_code, 200)
        data = listado.json()
        self.assertEqual(len(data['presets']), 1)
        self.assertEqual(data['presets'][0]['clave'], 'TITULAR')
        self.assertEqual(data['presets'][0]['nombre'], 'Titular')
        self.assertEqual(data['presets'][0]['pos1']['id'], self.jugadoras[0].id)
        self.assertIsNone(data['ultimo_set'])

    def test_guardar_titular_dos_veces_actualiza_sin_duplicar(self):
        url = reverse('stats_app:api_guardar_plantilla_rotacion', args=[self.partido.id])
        self.client.post(url, data=json.dumps(self.payload_titular), content_type='application/json')
        segundo = dict(self.payload_titular)
        segundo['pos1'] = self.jugadoras[5].id
        response = self.client.post(url, data=json.dumps(segundo), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LineupPreset.objects.filter(equipo=self.equipo).count(), 1)
        preset = LineupPreset.objects.get(equipo=self.equipo, clave='TITULAR')
        self.assertEqual(preset.pos1_id, self.jugadoras[5].id)

    def test_ultimo_set_es_el_inicial_del_partido_anterior_del_equipo(self):
        anterior = Partido.objects.create(
            equipo=self.equipo, fecha=date(2026, 1, 20), hora=time(18, 0),
            rival='CV Previo', local=True, lugar='Pabellón',
        )
        RotacionSet.objects.create(
            partido=anterior, set_numero=1, es_inicial=True,
            pos1=self.jugadoras[1], pos2=self.jugadoras[2], pos3=self.jugadoras[3],
            pos4=self.jugadoras[4], pos5=self.jugadoras[5], pos6=self.jugadoras[0],
        )
        RotacionSet.objects.create(
            partido=anterior, set_numero=3, es_inicial=True,
            pos1=self.jugadoras[6], pos2=self.jugadoras[0], pos3=self.jugadoras[1],
            pos4=self.jugadoras[2], pos5=self.jugadoras[3], pos6=self.jugadoras[4],
        )
        response = self.client.get(
            reverse('stats_app:api_plantillas_rotacion', args=[self.partido.id]),
            {'set': 1},
        )
        ultimo = response.json()['ultimo_set']
        self.assertEqual(ultimo['partido_id'], anterior.pk)
        self.assertEqual(ultimo['set_numero'], 3)
        self.assertEqual(ultimo['rival'], 'CV Previo')
        self.assertEqual(ultimo['pos1']['id'], self.jugadoras[6].id)

    def test_set_dos_usa_el_inicial_del_set_uno_del_mismo_partido(self):
        RotacionSet.objects.create(
            partido=self.partido, set_numero=1, es_inicial=True,
            pos1=self.jugadoras[0], pos2=self.jugadoras[1], pos3=self.jugadoras[2],
            pos4=self.jugadoras[3], pos5=self.jugadoras[4], pos6=self.jugadoras[5],
        )
        response = self.client.get(
            reverse('stats_app:api_plantillas_rotacion', args=[self.partido.id]),
            {'set': 2},
        )
        ultimo = response.json()['ultimo_set']
        self.assertEqual(ultimo['partido_id'], self.partido.pk)
        self.assertEqual(ultimo['set_numero'], 1)
        self.assertEqual(ultimo['pos1']['id'], self.jugadoras[0].id)

    def test_plantilla_ajena_no_aparece_en_el_listado(self):
        _, equipo_b, jugadora_b, _ = _crear_entrenador_con_partido('coach_preset_b')
        LineupPreset.objects.create(
            equipo=equipo_b, clave='TITULAR', nombre='Titular', orden=0,
            pos1=jugadora_b,
        )
        response = self.client.get(reverse('stats_app:api_plantillas_rotacion', args=[self.partido.id]))
        self.assertEqual(response.json()['presets'], [])

    def test_ultimo_set_no_usa_partidos_de_otro_entrenador(self):
        _, equipo_b, jugadora_b, partido_b = _crear_entrenador_con_partido('coach_preset_intruso')
        partido_b.fecha = date(2026, 8, 1)
        partido_b.save()
        RotacionSet.objects.create(
            partido=partido_b, set_numero=1, es_inicial=True, pos1=jugadora_b,
        )
        response = self.client.get(
            reverse('stats_app:api_plantillas_rotacion', args=[self.partido.id]),
            {'set': 1},
        )
        self.assertIsNone(response.json()['ultimo_set'])

    def test_guardar_con_jugadora_de_otro_equipo_es_rechazado(self):
        _, _, jugadora_ajena, _ = _crear_entrenador_con_partido('coach_preset_ajeno')
        payload = dict(self.payload_titular)
        payload['pos1'] = jugadora_ajena.id
        response = self.client.post(
            reverse('stats_app:api_guardar_plantilla_rotacion', args=[self.partido.id]),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(LineupPreset.objects.filter(equipo=self.equipo).exists())

    def test_guardar_plantilla_vacia_es_rechazado(self):
        response = self.client.post(
            reverse('stats_app:api_guardar_plantilla_rotacion', args=[self.partido.id]),
            data=json.dumps({'clave': 'ALINEACION_B'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)


class ReportingHelpersTests(TestCase):
    """Unidad para los cálculos nuevos de reporting.py: racha máxima del set
    y evolución del marcador (run chart), que se apoyan en el mismo criterio
    de qué registro representa un punto que ya usa `calc_set_score`."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido('coach_reporting')

    def _punto(self, accion, calidad):
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadora, tipo_fase='K1', accion=accion,
            calidad=calidad, set_numero=1,
        )

    def test_calc_racha_maxima_detecta_la_racha_mas_larga_no_solo_la_actual(self):
        # nosotros: 3 seguidos, luego rival: 2 seguidos, luego nosotros: 1
        for _ in range(3):
            self._punto('ATAQUE', '++')
        for _ in range(2):
            self._punto('ATAQUE', '--')
        self._punto('SAQUE', '++')

        resultado = calc_racha_maxima(self.partido, 1)
        self.assertEqual(resultado, {'lado': 'nosotros', 'racha': 3})

        # calc_racha (racha EN CURSO) debe ser distinta: solo 1, porque el
        # último punto fue nuestro tras la racha del rival.
        self.assertEqual(calc_racha(self.partido, 1), {'lado': None, 'racha': 0})

    def test_calc_racha_maxima_sin_rachas_devuelve_vacio(self):
        self._punto('ATAQUE', '++')
        self._punto('ATAQUE', '--')
        resultado = calc_racha_maxima(self.partido, 1)
        self.assertEqual(resultado, {'lado': None, 'racha': 0})

    def test_build_run_chart_acumula_diferencia_de_puntos_en_orden(self):
        self._punto('ATAQUE', '++')   # 1-0 -> +1
        self._punto('ATAQUE', '++')   # 2-0 -> +2
        self._punto('ATAQUE', '--')   # 2-1 -> +1
        self._punto('SAQUE', '++')    # 3-1 -> +2
        self.assertEqual(build_run_chart(self.partido, 1), [1, 2, 1, 2])

    def test_build_run_chart_ignora_acciones_sin_desenlace_de_punto(self):
        self._punto('RECEPCION', '+')  # acción neutra: no suma punto
        self._punto('ATAQUE', '++')
        self.assertEqual(build_run_chart(self.partido, 1), [1])

    def test_build_full_report_enriquece_cada_set_con_los_nuevos_bloques(self):
        self._punto('ATAQUE', '++')
        reporte = build_full_report(self.partido, 'global')
        self.assertEqual(len(reporte['detalle_sets']), 1)
        self.assertIsNone(reporte['detalle_total'])
        set_data = reporte['detalle_sets'][0]
        for clave in ('zonas', 'trazo', 'rotacion', 'racha_maxima', 'run_chart', 'k1_efi', 'k2_efi', 'lideres', 'destacados_por_accion'):
            self.assertIn(clave, set_data)
        self.assertEqual(len(set_data['zonas']), 6)
        self.assertEqual(len(set_data['rotacion']), 6)
        self.assertIn('saque', set_data['trazo'])
        self.assertIn('ataque', set_data['trazo'])
        self.assertIn('recepcion', set_data['trazo'])

    def test_trazo_analysis_destinos_y_flujos(self):
        """Solo ++ con zona_destino cuenta; errores y sin destino no contaminan %."""
        j = self.jugadora
        # 2 aces a Z5, 1 ace sin destino, 1 error saque (no cuenta)
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K0', accion='SAQUE',
            calidad='++', set_numero=1, zona=1, zona_destino=5, rotacion_num=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K0', accion='SAQUE',
            calidad='++', set_numero=1, zona=1, zona_destino=5, rotacion_num=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K0', accion='SAQUE',
            calidad='++', set_numero=1, zona=1, zona_destino=None, rotacion_num=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K0', accion='SAQUE',
            calidad='--', set_numero=1, zona=1, zona_destino=1, rotacion_num=1,
        )
        # Ataque Z4 → Z1 y recepción en Z6
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K1', accion='ATAQUE',
            calidad='++', set_numero=1, zona=4, zona_destino=1, rotacion_num=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K1', accion='RECEPCION',
            calidad='=', set_numero=1, zona=6, rotacion_num=1,
        )

        trazo = trazo_analysis(self.partido, 1)
        self.assertTrue(trazo['tiene_datos'])
        self.assertEqual(trazo['saque']['n'], 2)
        self.assertEqual(trazo['saque']['total'], 3)  # 3 aces; error no cuenta
        self.assertEqual(trazo['saque']['sin_destino'], 1)
        self.assertEqual(trazo['saque']['top_zona']['zona'], 5)
        self.assertEqual(trazo['ataque']['n'], 1)
        self.assertEqual(trazo['ataque']['top_zona']['zona'], 1)
        self.assertEqual(trazo['recepcion']['n'], 1)
        self.assertEqual(trazo['recepcion']['top_zona']['zona'], 6)
        self.assertEqual(trazo['flujos_ataque'][0]['label'], 'Z4 → Z1')
        self.assertEqual(trazo['flujos_ataque'][0]['n'], 1)
        self.assertTrue(trazo['matriz_ataque']['tiene_datos'])
        self.assertEqual(trazo['matriz_ataque']['n'], 1)
        celda_41 = trazo['matriz_ataque']['filas'][3]['celdas'][0]  # origen Z4, destino Z1
        self.assertEqual(trazo['matriz_ataque']['filas'][3]['origen'], 4)
        self.assertEqual(celda_41['destino'], 1)
        self.assertEqual(celda_41['n'], 1)
        self.assertIsNotNone(trazo['aviso_cobertura'])  # 1 ace sin destino
        self.assertIsNone(trazo['mensaje_vacio'])

    def test_trazo_analysis_mensaje_vacio_sin_destinos(self):
        """Si hay aces/puntos pero sin zona_destino, el informe explica por qué."""
        j = self.jugadora
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K0', accion='SAQUE',
            calidad='++', set_numero=1, zona=1, zona_destino=None, rotacion_num=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K1', accion='ATAQUE',
            calidad='++', set_numero=1, zona=4, zona_destino=None, rotacion_num=1,
        )
        trazo = trazo_analysis(self.partido, 1)
        self.assertFalse(trazo['tiene_datos'])
        self.assertFalse(trazo['matriz_ataque']['tiene_datos'])
        self.assertIn('sin destino', trazo['mensaje_vacio'].lower())
        self.assertEqual(trazo['aces_sin_destino'], 1)
        self.assertEqual(trazo['ataques_sin_destino'], 1)

    def test_build_full_report_incluye_detalle_total_con_varios_sets(self):
        j = Jugadora.objects.create(equipo=self.equipo, nombre='Test', dorsal=7)
        for set_n in (1, 2):
            for _ in range(3):
                RegistroEstadistica.objects.create(
                    partido=self.partido, jugadora=j, tipo_fase='K1',
                    accion='ATAQUE', calidad='++', set_numero=set_n,
                )
        reporte = build_full_report(self.partido, 'global')
        self.assertIsNotNone(reporte['detalle_total'])
        self.assertEqual(len(reporte['detalle_total']['jugadoras']), 1)
        self.assertEqual(reporte['detalle_total']['totales']['puntos'], 6)
        self.assertEqual(reporte['detalle_total']['totales']['ataque_kills'], 6)

    def test_build_set_leaders_prioriza_saldo_y_puntos_sobre_eficiencia_puntual(self):
        """Victoria 14/2 debe ganar a Lucía 1/1 en estrella y máxima anotadora."""
        v, l, b = Jugadora.objects.bulk_create([
            Jugadora(equipo=self.equipo, nombre='Victoria', dorsal=12),
            Jugadora(equipo=self.equipo, nombre='Lucía', dorsal=18),
            Jugadora(equipo=self.equipo, nombre='Belén', dorsal=25),
        ])
        acciones = [
            (v, 'ATAQUE', '++'), (v, 'ATAQUE', '++'), (v, 'ATAQUE', '++'),
            (v, 'ATAQUE', '++'), (v, 'ATAQUE', '--'), (v, 'ATAQUE', '--'),
            (l, 'ATAQUE', '++'), (l, 'SAQUE', '++'),
            (b, 'RECEPCION', '+'), (b, 'RECEPCION', '+'), (b, 'RECEPCION', '+'),
            (b, 'RECEPCION', '+'), (b, 'RECEPCION', '+'), (b, 'RECEPCION', '+'),
            (b, 'RECEPCION', '+'), (b, 'RECEPCION', '+'), (b, 'RECEPCION', '-'),
            (b, 'SAQUE', '--'),
        ]
        for jug, accion, cal in acciones:
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=jug, tipo_fase='K1',
                accion=accion, calidad=cal, set_numero=1,
            )

        lideres = build_set_leaders(self.partido, 1)
        self.assertEqual(lideres['estrella']['dorsal'], 12)
        self.assertEqual(lideres['maxima_anotadora']['dorsal'], 12)
        self.assertEqual(lideres['mejor_saque']['dorsal'], 18)

        dest = build_destacados_por_accion(self.partido, 1)
        self.assertEqual(dest['ataque']['mejor']['dorsal'], 12)
        self.assertEqual(dest['saque']['mejor']['dorsal'], 18)
        self.assertIsNone(dest['saque']['a_mejorar'])  # solo 1 error de saque
        self.assertEqual(dest['recepcion']['mejor']['dorsal'], 25)
        self.assertIn('defensa', dest)
        self.assertIn('bloqueo', dest)

    def test_destacados_mejor_recepcion_prioriza_volumen_con_eficacia_minima(self):
        """100 rec al 80% gana a 12 rec perfectas: más peso en recepción."""
        libero = Jugadora.objects.create(equipo=self.equipo, nombre='Líbero', dorsal=29, posicion='LIBERO')
        otra = Jugadora.objects.create(equipo=self.equipo, nombre='Lucía', dorsal=18)
        for _ in range(80):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=libero, tipo_fase='K1',
                accion='RECEPCION', calidad='+', set_numero=1,
            )
        for _ in range(20):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=libero, tipo_fase='K1',
                accion='RECEPCION', calidad='--', set_numero=1,
            )
        for _ in range(12):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=otra, tipo_fase='K1',
                accion='RECEPCION', calidad='++', set_numero=1,
            )

        dest = build_destacados_por_accion(self.partido, 1)
        self.assertEqual(dest['recepcion']['mejor']['dorsal'], 29)

    def test_destacados_mejor_defensa_prioriza_volumen_con_eficacia_minima(self):
        """Más defensas con >=80% eficacia gana a pocas defensas perfectas."""
        libero = Jugadora.objects.create(equipo=self.equipo, nombre='Líbero', dorsal=29, posicion='LIBERO')
        otra = Jugadora.objects.create(equipo=self.equipo, nombre='Lucía', dorsal=18)
        for _ in range(50):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=libero, tipo_fase='K2',
                accion='DEFENSA', calidad='+', set_numero=1,
            )
        for _ in range(10):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=libero, tipo_fase='K2',
                accion='DEFENSA', calidad='--', set_numero=1,
            )
        for _ in range(12):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=otra, tipo_fase='K2',
                accion='DEFENSA', calidad='+', set_numero=1,
            )

        dest = build_destacados_por_accion(self.partido, 1)
        self.assertEqual(dest['defensa']['mejor']['dorsal'], 29)

    def test_candidata_cambio_no_penaliza_libero_con_alta_eficacia_defensiva(self):
        """26 defensas buenas y 2 errores (saldo -2) no debe marcar fila roja."""
        ana = Jugadora.objects.create(equipo=self.equipo, nombre='Ana', dorsal=29, posicion='LIBERO')
        for _ in range(26):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=ana, tipo_fase='K2',
                accion='DEFENSA', calidad='+', set_numero=1,
            )
        for _ in range(2):
            RegistroEstadistica.objects.create(
                partido=self.partido, jugadora=ana, tipo_fase='K2',
                accion='DEFENSA', calidad='--', set_numero=1,
            )

        fila = build_quick_set_report(self.partido, 1)['tabla_rapida']
        ana_row = next(r for r in fila if r['dorsal'] == 29)
        self.assertEqual(ana_row['balance'], -2)
        self.assertEqual(ana_row['defensas'], 26)
        self.assertFalse(ana_row['alerta'])

    def test_candidata_cambio_si_penaliza_jugadora_sin_volumen_defensivo(self):
        p = {'balance': -2, 'puntos': 0, 'errores': 2, 'defensas': 1, 'defensa_err': 1,
             'recepcion_pos': 0, 'recepcion_err': 0, 'asistencias': 0, 'colocacion_err': 0}
        self.assertTrue(_candidata_cambio(p))

    def test_build_partido_snapshot_detecta_set_activo_y_marcador(self):
        for _ in range(25):
            self._punto('ATAQUE', '++')
        for _ in range(17):
            self._punto('ATAQUE', '--')

        snap = build_partido_snapshot(self.partido)
        self.assertEqual(snap['set_activo'], 1)
        self.assertEqual(snap['puntos_local'], 25)
        self.assertEqual(snap['puntos_rival'], 17)
        self.assertEqual(snap['sets_local'], 1)

    def test_marcador_resumen_sin_scout_no_inventa_resultado(self):
        resumen = marcador_resumen(self.partido)
        self.assertFalse(resumen['tiene_scout'])
        self.assertEqual(resumen['parciales'], [])
        self.assertIsNone(resumen['victoria'])

    def test_marcador_resumen_devuelve_sets_y_parciales(self):
        for _ in range(25):
            self._punto('ATAQUE', '++')
        for _ in range(18):
            self._punto('ATAQUE', '--')
        RegistroEstadistica.objects.bulk_create([
            RegistroEstadistica(
                partido=self.partido, jugadora=self.jugadora, tipo_fase='K1',
                accion='ATAQUE', calidad='++' if i < 22 else '--', set_numero=2,
            )
            for i in range(47)
        ])
        self.partido._reporting_rows_cache = None
        self.partido._reporting_rows_by_set_cache = None

        resumen = marcador_resumen(self.partido)
        self.assertTrue(resumen['tiene_scout'])
        self.assertEqual(resumen['sets_local'], 1)
        self.assertEqual(resumen['sets_rival'], 1)
        self.assertEqual(resumen['parciales'][0], {'set': 1, 'local': 25, 'rival': 18})
        self.assertEqual(resumen['parciales'][1], {'set': 2, 'local': 22, 'rival': 25})
        self.assertIsNone(resumen['victoria'])

    def test_build_full_report_no_hace_n_mas_1_queries(self):
        """Regresión de rendimiento: el informe completo generaba miles de
        queries (una por cada combinación set × jugadora × fundamento ×
        calidad), lo que bloqueaba la app en producción contra una BD remota
        con latencia de red por consulta. Debe resolverse con un puñado de
        queries fijas, sin importar cuántos sets/jugadoras/acciones haya."""
        jugadoras = [
            Jugadora.objects.create(equipo=self.equipo, nombre=f'J{i}', dorsal=i)
            for i in range(1, 9)
        ]
        acciones = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
        calidades = ['++', '+', '=', '-', '--']
        fases = ['K0', 'K1', 'K2']
        for set_n in (1, 2, 3):
            registros = [
                RegistroEstadistica(
                    partido=self.partido,
                    jugadora=jugadoras[i % len(jugadoras)],
                    set_numero=set_n,
                    tipo_fase=fases[i % len(fases)],
                    accion=acciones[i % len(acciones)],
                    calidad=calidades[i % len(calidades)],
                    rotacion_num=(i % 6) + 1,
                    zona=(i % 6) + 1,
                )
                for i in range(150)
            ]
            RegistroEstadistica.objects.bulk_create(registros)
            RotacionSet.objects.create(
                partido=self.partido, set_numero=set_n,
                pos1=jugadoras[0], pos2=jugadoras[1], pos3=jugadoras[2],
                pos4=jugadoras[3], pos5=jugadoras[4], pos6=jugadoras[5],
            )

        with CaptureQueriesContext(connection) as ctx:
            reporte = build_full_report(self.partido, 'global')

        self.assertEqual(len(reporte['detalle_sets']), 3)
        self.assertLess(
            len(ctx.captured_queries), 10,
            f"build_full_report ejecutó {len(ctx.captured_queries)} queries; "
            "no debería escalar con el número de sets/jugadoras/acciones."
        )

    def test_ajuste_marcador_suma_y_resta_sin_contar_como_merito(self):
        """AJUSTE_MARCADOR mueve el marcador sin pasar por Acciones/mérito."""
        self._punto('ATAQUE', '++')
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=None, tipo_fase='K1',
            accion='AJUSTE_MARCADOR', calidad='++', set_numero=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=None, tipo_fase='K1',
            accion='AJUSTE_MARCADOR', calidad='--', set_numero=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=None, tipo_fase='K1',
            accion='AJUSTE_MARCADOR', calidad='+', set_numero=1,
        )
        local, rival = calc_set_score(self.partido, 1)
        self.assertEqual((local, rival), (1, 1))
        merito, err_rival = merito_y_error_rival(self.partido, 1)
        self.assertEqual((merito, err_rival), (1, 0))

    def test_accion_red_cuenta_como_punto_para_el_rival(self):
        """RED = la jugadora seleccionada ha tocado la red: punto directo
        para el rival, igual que un error nuestro cualquiera."""
        j = Jugadora.objects.create(equipo=self.equipo, nombre='Ana', dorsal=4)
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K1',
            accion='RED', calidad='--', set_numero=1,
        )
        local, rival = calc_set_score(self.partido, 1)
        self.assertEqual((local, rival), (0, 1))

    def test_accion_red_cuenta_como_error_individual_de_la_jugadora(self):
        """El toque de red debe reflejarse en el balance/errores de la
        jugadora en el informe, aunque no tenga columna propia."""
        j = Jugadora.objects.create(equipo=self.equipo, nombre='Ana', dorsal=4)
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=1,
        )
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=j, tipo_fase='K1',
            accion='RED', calidad='--', set_numero=1,
        )
        report = build_set_report(self.partido, 1)
        fila = next(row for row in report['jugadoras'] if row['dorsal'] == 4)
        self.assertEqual(fila['puntos'], 1)
        self.assertEqual(fila['errores'], 1)
        self.assertEqual(fila['balance'], 0)
        self.assertEqual(fila['acciones'], 2)

    def test_resumen_sets_incluye_puntos_merito_y_error_rival_por_set(self):
        self._punto('ATAQUE', '++')      # mérito
        self._punto('ERROR_RIVAL', '')   # error rival
        reporte = build_full_report(self.partido, 'global')
        set_resumen = reporte['resumen_sets'][0]
        self.assertEqual(set_resumen['puntos_merito'], 1)
        self.assertEqual(set_resumen['puntos_err_rival'], 1)
        self.assertEqual(reporte['resumen_totales']['puntos_merito'], 1)
        self.assertEqual(reporte['resumen_totales']['puntos_err_rival'], 1)

    def test_build_quick_report_es_alias_de_build_full_report(self):
        self.assertIs(build_quick_report, build_full_report)

    def test_build_advanced_report_incluye_desglose_calidad_y_red(self):
        self._punto('RECEPCION', '+')
        self._punto('RECEPCION', '=')
        self._punto('ATAQUE', '++')
        RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadora, set_numero=1,
            tipo_fase='K1', accion='RED', calidad='--', rotacion_num=1,
        )
        reporte = build_advanced_report(self.partido, 'global')
        set_data = reporte['detalle_sets'][0]
        self.assertEqual(set_data['red_equipo'], 1)
        jug = set_data['jugadoras'][0]
        self.assertEqual(jug['red'], 1)
        self.assertIn('RECEPCION', jug['fundamentos'])
        self.assertEqual(jug['fundamentos']['RECEPCION']['p'], 1)
        self.assertEqual(jug['fundamentos']['RECEPCION']['eq'], 1)
        self.assertIn('fundamentos_meta', reporte)
        self.assertEqual(len(reporte['fundamentos_meta']), 6)


def _puntos_set(partido, jugadora, set_n, local, rival):
    """Crea puntos de ataque ++ / -- para cerrar un parcial en tests."""
    registros = [
        RegistroEstadistica(
            partido=partido, jugadora=jugadora, tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=set_n,
        )
        for _ in range(local)
    ] + [
        RegistroEstadistica(
            partido=partido, jugadora=jugadora, tipo_fase='K1',
            accion='ATAQUE', calidad='--', set_numero=set_n,
        )
        for _ in range(rival)
    ]
    RegistroEstadistica.objects.bulk_create(registros)


class DashboardSprint1Tests(TestCase):
    """Héroe del próximo cruce, pestañas y XSS en dashboard."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido(
            'coach_dash'
        )
        self.client.login(username='coach_dash', password='pass12345')
        self.hoy = timezone.localdate()

    def test_heroe_muestra_el_proximo_pendiente_y_no_lo_repite_en_la_lista(self):
        self.partido.rival = 'CV Alcorcón'
        self.partido.lugar = 'Pabellón Sur'
        self.partido.fecha = self.hoy
        self.partido.hora = time(10, 30)
        self.partido.save()
        mas_tarde = Partido.objects.create(
            equipo=self.equipo, fecha=self.hoy + timedelta(days=7), hora=time(18, 0),
            rival='Otro Rival', local=True, lugar='Pabellón Norte',
        )

        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['proximo_partido'].pk, self.partido.pk)
        ids_lista = [p.pk for p in response.context['partidos_proximos']]
        self.assertEqual(ids_lista, [mas_tarde.pk])
        self.assertContains(response, 'Próximo cruce')
        self.assertContains(response, 'CV Alcorcón')
        self.assertContains(response, 'Scout en vivo')
        self.assertContains(response, 'Preparar partido')
        self.assertContains(
            response,
            reverse('stats_app:modo_partido', args=[self.partido.pk]) + '?tab=rotacion',
        )
        self.assertContains(response, 'Pabellón Sur')
        self.assertContains(response, reverse('stats_app:partido_editar', args=[self.partido.pk]))
        self.assertContains(response, reverse('stats_app:partido_eliminar', args=[self.partido.pk]))
        self.assertNotContains(response, 'Por scoutar')

    def test_pasado_sin_finalizar_va_a_por_scoutar_no_al_heroe(self):
        self.partido.rival = 'CV Alevín'
        self.partido.fecha = self.hoy - timedelta(days=40)
        self.partido.hora = time(11, 0)
        self.partido.save()
        futuro = Partido.objects.create(
            equipo=self.equipo, fecha=self.hoy + timedelta(days=2), hora=time(12, 0),
            rival='CV Alcorcón', local=True, lugar='Pabellón Sur',
        )

        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertEqual(response.context['proximo_partido'].pk, futuro.pk)
        self.assertEqual(list(response.context['partidos_proximos']), [])
        ids_pendientes = [p.pk for p in response.context['partidos_por_scoutar']]
        self.assertEqual(ids_pendientes, [self.partido.pk])
        self.assertContains(response, 'Por scoutar')
        self.assertEqual(response.context['tab_partidos_inicial'], 'proximos')

    def test_pestaña_por_scoutar_oculta_si_no_hay_atrasados(self):
        self.partido.fecha = self.hoy
        self.partido.save()

        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertEqual(list(response.context['partidos_por_scoutar']), [])
        self.assertNotContains(response, 'Por scoutar')
        self.assertEqual(response.context['tab_partidos_inicial'], 'proximos')

    def test_solo_atrasados_abre_por_scoutar_sin_heroe(self):
        self.partido.fecha = self.hoy - timedelta(days=3)
        self.partido.save()

        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertIsNone(response.context['proximo_partido'])
        self.assertEqual(list(response.context['partidos_proximos']), [])
        self.assertEqual(
            [p.pk for p in response.context['partidos_por_scoutar']],
            [self.partido.pk],
        )
        self.assertEqual(response.context['tab_partidos_inicial'], 'por-scoutar')
        self.assertNotContains(response, 'Próximo cruce')
        self.assertContains(response, 'Por scoutar')

    def test_heroe_ausente_si_solo_hay_historial(self):
        self.partido.finalizado = True
        self.partido.save()

        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertIsNone(response.context['proximo_partido'])
        self.assertEqual(list(response.context['partidos_proximos']), [])
        self.assertEqual(list(response.context['partidos_por_scoutar']), [])
        self.assertEqual(len(response.context['partidos_historial']), 1)
        self.assertNotContains(response, 'Próximo cruce')
        self.assertNotContains(response, 'Por scoutar')
        self.assertContains(response, 'Sin scout registrado')

    def test_historial_muestra_marcador_y_parciales(self):
        self.partido.finalizado = True
        self.partido.rival = 'CV Final'
        self.partido.save()
        _puntos_set(self.partido, self.jugadora, 1, 25, 18)
        _puntos_set(self.partido, self.jugadora, 2, 22, 25)
        _puntos_set(self.partido, self.jugadora, 3, 25, 19)
        _puntos_set(self.partido, self.jugadora, 4, 25, 21)

        response = self.client.get(reverse('stats_app:dashboard'))
        historial = response.context['partidos_historial']
        self.assertEqual(len(historial), 1)
        marcador = historial[0].marcador
        self.assertTrue(marcador['tiene_scout'])
        self.assertEqual(marcador['sets_local'], 3)
        self.assertEqual(marcador['sets_rival'], 1)
        self.assertTrue(marcador['victoria'])
        self.assertContains(response, '3 – 1')
        self.assertContains(response, '25-18')
        self.assertContains(response, '22-25')
        self.assertContains(response, '25-19')
        self.assertContains(response, '25-21')
        self.assertContains(response, f'{self.equipo.nombre} vs CV Final')
        self.assertContains(response, 'CV Final')

    def test_rival_xss_en_heroe_se_escapa(self):
        payload = "<img src=x onerror=alert(1)>"
        self.partido.rival = payload
        self.partido.fecha = self.hoy
        self.partido.save()

        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertNotContains(response, payload)
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')
        self.assertEqual(response.context['proximo_partido'].pk, self.partido.pk)


class CrudAdministracionTests(TestCase):
    """CRUDs de Equipo/Jugadora/Partido: flujo feliz (alta/edición/baja)
    sobre recursos propios, complementando los tests de aislamiento (que
    solo cubren el camino de acceso denegado sobre recursos ajenos)."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido('coach_crud')
        self.client.login(username='coach_crud', password='pass12345')

    def test_configuracion_view_renderiza(self):
        response = self.client.get(reverse('stats_app:configuracion'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ayuda y legal')
        self.assertContains(response, reverse('stats_app:descargar_manual_usuario'))
        self.assertContains(response, 'stats_app/vendor/lucide.min.js')
        dashboard = self.client.get(reverse('stats_app:dashboard'))
        self.assertNotContains(dashboard, 'Manual de usuario')

    def test_editar_equipo_propio(self):
        response = self.client.post(reverse('stats_app:equipo_editar', args=[self.equipo.pk]), data={
            'nombre': 'Nuevo Nombre', 'temporada': '2026/2027', 'categoria': 'JUNIOR',
        })
        self.assertEqual(response.status_code, 302)
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.nombre, 'Nuevo Nombre')

    def test_eliminar_equipo_propio(self):
        response = self.client.post(reverse('stats_app:equipo_eliminar', args=[self.equipo.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Equipo.objects.filter(pk=self.equipo.pk).exists())

    def test_crear_editar_eliminar_jugadora_propia(self):
        crear = self.client.post(reverse('stats_app:jugadora_nueva'), data={
            'equipo': self.equipo.id, 'nombre': 'Nueva', 'apellidos': 'Jugadora',
            'dorsal': 99, 'posicion': 'OPUESTA', 'fecha_nacimiento': '2006-05-05',
        })
        self.assertEqual(crear.status_code, 302)
        self.assertIn(f'equipo_id={self.equipo.id}', crear.url)
        nueva = Jugadora.objects.get(nombre='Nueva')

        editar = self.client.post(reverse('stats_app:jugadora_editar', args=[nueva.pk]), data={
            'equipo': self.equipo.id, 'nombre': 'Editada', 'apellidos': 'Jugadora',
            'dorsal': 99, 'posicion': 'OPUESTA', 'fecha_nacimiento': '2006-05-05',
        })
        self.assertEqual(editar.status_code, 302)
        self.assertEqual(editar.url, reverse('stats_app:equipos_list'))
        nueva.refresh_from_db()
        self.assertEqual(nueva.nombre, 'Editada')

        eliminar = self.client.post(reverse('stats_app:jugadora_eliminar', args=[nueva.pk]))
        self.assertEqual(eliminar.status_code, 302)
        self.assertFalse(Jugadora.objects.filter(pk=nueva.pk).exists())

    def test_crear_editar_eliminar_partido_propio(self):
        crear = self.client.post(reverse('stats_app:partido_nuevo'), data={
            'equipo': self.equipo.id, 'fecha': '2026-05-01', 'hora': '19:00',
            'rival': 'Nuevo Rival', 'local': True, 'lugar': 'Otro pabellón', 'modalidad': 'VOLEY',
        })
        self.assertEqual(crear.status_code, 302)
        nuevo = Partido.objects.get(rival='Nuevo Rival')

        editar = self.client.post(reverse('stats_app:partido_editar', args=[nuevo.pk]), data={
            'equipo': self.equipo.id, 'fecha': '2026-05-02', 'hora': '20:00',
            'rival': 'Rival Editado', 'local': False, 'lugar': 'Otro pabellón', 'modalidad': 'VOLEY',
        })
        self.assertEqual(editar.status_code, 302)
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.rival, 'Rival Editado')

        eliminar = self.client.post(reverse('stats_app:partido_eliminar', args=[nuevo.pk]))
        self.assertEqual(eliminar.status_code, 302)
        self.assertFalse(Partido.objects.filter(pk=nuevo.pk).exists())


# ─────────────────────────────────────────────────────────────────────────────
# VALIDACIÓN DE RANGO DE NEGOCIO, AUDITORÍA Y NO FUGA DE INFORMACIÓN TÉCNICA
#
# Cierra los últimos flecos de seguridad/observabilidad pedidos:
#   1. Límites reales del reglamento de voleibol en los formularios de la API
#      (zonas 1-6 / 1-4, puntos por set, sets para ganar).
#   2. Logs de auditoría (`logger.warning`) con la IP del atacante en cada
#      bloqueo de rate limit (429) y cada acceso IDOR detectado (404 forzado).
#   3. Ninguna respuesta de error revela el detalle interno de una excepción.
# ─────────────────────────────────────────────────────────────────────────────
class ValidacionRangoDeNegocioTests(TestCase):
    """Un payload sintácticamente válido (enteros) pero incoherente con el
    reglamento real de voleibol (zonas fuera de 1-6, sets al mejor de 50...)
    debe rechazarse igual que un tipo de dato incorrecto."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido('coach_rango')
        self.client.login(username='coach_rango', password='pass12345')

    def test_form_registrar_accion_rechaza_rotacion_num_mayor_que_seis(self):
        form = RegistrarAccionForm({
            'partido_id': self.partido.id, 'accion': 'ATAQUE', 'rotacion_num': 7,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('rotacion_num', form.errors)

    def test_form_registrar_accion_rechaza_rotacion_num_negativa(self):
        form = RegistrarAccionForm({
            'partido_id': self.partido.id, 'accion': 'ATAQUE', 'rotacion_num': -1,
        })
        self.assertFalse(form.is_valid())

    def test_form_registrar_accion_rechaza_set_numero_gigante(self):
        form = RegistrarAccionForm({
            'partido_id': self.partido.id, 'accion': 'ATAQUE', 'set_numero': 999999,
        })
        self.assertFalse(form.is_valid())

    def test_config_set_rechaza_puntos_por_set_gigantes(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_config_set', args=[self.partido.id]),
            data=json.dumps({'puntos_por_set': 999999, 'puntos_set_decisivo': 15, 'sets_para_ganar': 3}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_config_set_rechaza_sets_para_ganar_incoherente(self):
        """Ningún formato oficial de voleibol se juega "al mejor de 50"."""
        response = self.client.post(
            reverse('stats_app:api_actualizar_config_set', args=[self.partido.id]),
            data=json.dumps({'puntos_por_set': 25, 'puntos_set_decisivo': 15, 'sets_para_ganar': 50}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_registrar_accion_rechaza_rotacion_num_fuera_de_rango_voley(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id, 'accion': 'ATAQUE', 'rotacion_num': 9,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_registrar_accion_guarda_zona_destino(self):
        """Modo Trazo: saque/ataque pueden guardar zona rival (destino)."""
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id,
                'jugadora_id': self.jugadora.id,
                'accion': 'SAQUE',
                'calidad': '++',
                'set_numero': 1,
                'fase': 'K0',
                'rotacion_num': 1,
                'zona': 1,
                'zona_destino': 5,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        reg = RegistroEstadistica.objects.get(id=response.json()['id'])
        self.assertEqual(reg.zona, 1)
        self.assertEqual(reg.zona_destino, 5)

    def test_registrar_accion_rechaza_zona_destino_fuera_de_rango(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id, 'accion': 'ATAQUE', 'calidad': '++',
                'zona_destino': 9,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_registrar_accion_en_minivoley_rechaza_rotacion_num_mayor_que_cuatro(self):
        """Zonas de minivoley: 1-4. rotacion_num=5 o 6 pasa el límite genérico
        del formulario (máx. universal 6) pero no el reglamento real de esta
        modalidad, que solo se conoce tras resolver el partido."""
        equipo_mini = Equipo.objects.create(
            entrenador=self.coach, nombre='Mini Rango', temporada='2025/2026', categoria='BENJAMIN'
        )
        partido_mini = Partido.objects.create(
            equipo=equipo_mini, fecha=date(2026, 3, 1), hora=time(17, 0),
            rival='Rival Mini', local=True, lugar='Sala', modalidad='MINIVOLEY',
        )
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': partido_mini.id, 'accion': 'ATAQUE', 'rotacion_num': 5,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_registrar_accion_en_minivoley_acepta_rotacion_num_valida(self):
        equipo_mini = Equipo.objects.create(
            entrenador=self.coach, nombre='Mini Rango OK', temporada='2025/2026', categoria='BENJAMIN'
        )
        partido_mini = Partido.objects.create(
            equipo=equipo_mini, fecha=date(2026, 3, 1), hora=time(17, 0),
            rival='Rival Mini', local=True, lugar='Sala', modalidad='MINIVOLEY',
        )
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': partido_mini.id, 'accion': 'ATAQUE', 'rotacion_num': 4,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)


class AuditoriaDeSeguridadTests(TestCase):
    """Todo bloqueo de rate limit (429) y todo acceso IDOR (404 forzado)
    debe dejar constancia en el logger `stats_app.security`, incluyendo la
    IP del cliente, para poder configurar alertas en Cloud Run Logging."""

    def setUp(self):
        cache.clear()
        self.coach_a = User.objects.create_user(username='audit_a', password='pass12345')
        _, self.equipo_b, self.jugadora_b, self.partido_b = _crear_entrenador_con_partido('audit_b')
        self.client.login(username='audit_a', password='pass12345')

    def tearDown(self):
        cache.clear()

    def test_idor_sobre_partido_ajeno_genera_log_de_seguridad_con_ip(self):
        with self.assertLogs('stats_app.security', level='WARNING') as logs:
            response = self.client.get(
                reverse('stats_app:api_get_rotacion', args=[self.partido_b.id]),
                REMOTE_ADDR='6.6.6.6',
            )
        self.assertEqual(response.status_code, 404)
        mensaje = '\n'.join(logs.output)
        self.assertIn('IDOR', mensaje)
        self.assertIn('Partido', mensaje)
        self.assertIn(str(self.partido_b.id), mensaje)

    def test_idor_sobre_jugadora_ajena_en_rate_limited_generico_genera_log(self):
        with self.assertLogs('stats_app.security', level='WARNING') as logs:
            response = self.client.post(
                reverse('stats_app:api_actualizar_pos_jugadora'),
                data=json.dumps({'jugadora_id': self.jugadora_b.id, 'posicion': 'LIBERO'}),
                content_type='application/json',
                REMOTE_ADDR='7.7.7.7',
            )
        self.assertEqual(response.status_code, 404)
        mensaje = '\n'.join(logs.output)
        self.assertIn('Jugadora', mensaje)

    def test_bloqueo_por_rate_limit_genera_log_de_seguridad_con_ip(self):
        with override_settings(RATE_LIMIT_RULES=[(r'^/api/', 1, 60)]):
            mw = RateLimitMiddleware(get_response=lambda r: HttpResponse('ok'))
            request = RequestFactory().get('/api/algo/', REMOTE_ADDR='8.8.4.4')
            mw(request)
            with self.assertLogs('stats_app.security', level='WARNING') as logs:
                bloqueada = mw(request)
        self.assertEqual(bloqueada.status_code, 429)
        mensaje = '\n'.join(logs.output)
        self.assertIn('Rate limit', mensaje)
        self.assertIn('8.8.4.4', mensaje)


class OcultacionDeDetalleInternoTests(TestCase):
    """OWASP A05: Security Misconfiguration. Ninguna respuesta de error debe
    filtrar rutas de fichero, tracebacks o mensajes crudos de excepciones de
    bajo nivel (SQL, tipos, etc.) al cliente, aunque sí deben registrarse
    íntegros en el log del servidor para diagnóstico."""

    def test_ocultar_detalle_interno_devuelve_mensaje_generico_en_produccion(self):
        from .security import ocultar_detalle_interno
        with override_settings(DEBUG=False):
            mensaje = ocultar_detalle_interno(ValueError('/ruta/secreta/settings.py: columna x_secreta no existe'))
        self.assertNotIn('ruta/secreta', mensaje)
        self.assertNotIn('x_secreta', mensaje)

    def test_ocultar_detalle_interno_devuelve_mensaje_real_en_debug(self):
        from .security import ocultar_detalle_interno
        with override_settings(DEBUG=True):
            mensaje = ocultar_detalle_interno(ValueError('detalle técnico útil solo en local'))
        self.assertIn('detalle técnico útil solo en local', mensaje)


class AutenticacionYFlujoJugadorasTests(TestCase):
    """Login con email, recuperación de contraseña, CSRF amigable y flujo de altas."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username='coach_login',
            email='coach@example.com',
            password='ContraseñaSegura123!',
        )
        self.equipo = Equipo.objects.create(
            entrenador=self.usuario,
            nombre='Equipo Login',
            temporada='2025/2026',
            categoria='SENIOR',
        )

    def test_login_con_nombre_de_usuario(self):
        response = self.client.post(reverse('login'), {
            'username': 'coach_login',
            'password': 'ContraseñaSegura123!',
        })
        self.assertRedirects(response, reverse('stats_app:dashboard'))

    def test_login_con_correo_electronico(self):
        response = self.client.post(reverse('login'), {
            'username': 'coach@example.com',
            'password': 'ContraseñaSegura123!',
        })
        self.assertRedirects(response, reverse('stats_app:dashboard'))

    def test_solicitud_recuperacion_contraseña_envia_correo(self):
        with self.settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.client.post(reverse('password_reset'), {'email': 'coach@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('coach@example.com', mail.outbox[0].to)

    def test_csrf_invalido_redirige_al_login_con_mensaje(self):
        self.client.login(username='coach_login', password='ContraseñaSegura123!')
        cliente_csrf = Client(enforce_csrf_checks=True)
        cliente_csrf.login(username='coach_login', password='ContraseñaSegura123!')
        response = cliente_csrf.post(reverse('stats_app:jugadora_nueva'), {
            'equipo': self.equipo.id,
            'nombre': 'Sin',
            'apellidos': 'CSRF',
            'dorsal': 1,
            'posicion': 'CENTRAL',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('login'))

        seguimiento = cliente_csrf.get(response.url, follow=True)
        self.assertContains(seguimiento, 'Tu sesión ha cambiado o caducado')

    def test_crear_jugadora_sin_fecha_nacimiento(self):
        self.client.login(username='coach_login', password='ContraseñaSegura123!')
        response = self.client.post(
            reverse('stats_app:jugadora_nueva') + f'?equipo_id={self.equipo.id}',
            data={
                'equipo': self.equipo.id,
                'nombre': 'Sin',
                'apellidos': 'Fecha',
                'dorsal': 7,
                'posicion': 'LIBERO',
            },
        )
        self.assertEqual(response.status_code, 302)
        jugadora = Jugadora.objects.get(nombre='Sin', apellidos='Fecha')
        self.assertIsNone(jugadora.fecha_nacimiento)
        self.assertIn(f'equipo_id={self.equipo.id}', response.url)

    def test_crear_jugadora_redirige_al_formulario_del_mismo_equipo(self):
        self.client.login(username='coach_login', password='ContraseñaSegura123!')
        response = self.client.post(
            reverse('stats_app:jugadora_nueva') + f'?equipo_id={self.equipo.id}',
            data={
                'equipo': self.equipo.id,
                'nombre': 'Otra',
                'apellidos': 'Jugadora',
                'dorsal': 8,
                'posicion': 'RECEPTORA',
                'fecha_nacimiento': '2010-03-15',
            },
            follow=True,
        )
        self.assertContains(response, 'añadida correctamente')
        self.assertContains(response, 'Datos de la Nueva Jugadora')


class PartidoFinalizadoBloqueaMutacionesTests(TestCase):
    """Tras finalizar, las APIs de escritura deben rechazar cambios hasta reabrir."""

    def setUp(self):
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido('coach_fin')
        self.jugadora_b = Jugadora.objects.create(
            equipo=self.equipo, nombre='B', apellidos='Bench', dorsal=9, posicion='RECEPTORA',
        )
        RotacionSet.objects.create(
            partido=self.partido, set_numero=1, es_inicial=True,
            pos1=self.jugadora, pos2=self.jugadora_b, pos3=self.jugadora,
            pos4=self.jugadora_b, pos5=self.jugadora, pos6=self.jugadora_b,
        )
        self.registro = RegistroEstadistica.objects.create(
            partido=self.partido, jugadora=self.jugadora, tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=1,
        )
        self.nota = NotaPartido.objects.create(
            partido=self.partido, texto='Nota previa', set_numero=1,
        )
        self.partido.finalizado = True
        self.partido.save(update_fields=['finalizado'])
        self.client.login(username='coach_fin', password='pass12345')

    def test_registrar_accion_rechazada_si_finalizado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id, 'jugadora_id': self.jugadora.id,
                'accion': 'SAQUE', 'calidad': '++', 'fase': 'K0', 'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('finalizado', response.json().get('mensaje', '').lower())
        self.assertEqual(RegistroEstadistica.objects.filter(partido=self.partido).count(), 1)

    def test_eliminar_accion_rechazada_si_finalizado(self):
        response = self.client.post(
            reverse('stats_app:api_eliminar_estadistica'),
            data=json.dumps({'id': self.registro.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(RegistroEstadistica.objects.filter(pk=self.registro.pk).exists())

    def test_registrar_cambio_rechazado_si_finalizado(self):
        response = self.client.post(
            reverse('stats_app:api_registrar_cambio'),
            data=json.dumps({
                'partido_id': self.partido.id,
                'sale_id': self.jugadora.id,
                'entra_id': self.jugadora_b.id,
                'rotacion_num': 1,
                'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_config_set_rechazada_si_finalizado(self):
        response = self.client.post(
            reverse('stats_app:api_actualizar_config_set', args=[self.partido.id]),
            data=json.dumps({
                'puntos_por_set': 25, 'puntos_set_decisivo': 15, 'sets_para_ganar': 3,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_rotar_y_guardar_alineacion_rechazados_si_finalizado(self):
        rot = self.client.post(
            reverse('stats_app:api_rotar_manual', args=[self.partido.id]),
            data=json.dumps({'set_numero': 1, 'direccion': 'horario'}),
            content_type='application/json',
        )
        self.assertEqual(rot.status_code, 400)

        alin = self.client.post(
            reverse('stats_app:api_guardar_rotacion_inicial', args=[self.partido.id]),
            data=json.dumps({
                'set_numero': 1,
                'pos1': self.jugadora.id, 'pos2': self.jugadora_b.id,
                'pos3': self.jugadora.id, 'pos4': self.jugadora_b.id,
                'pos5': self.jugadora.id, 'pos6': self.jugadora_b.id,
            }),
            content_type='application/json',
        )
        self.assertEqual(alin.status_code, 400)

    def test_notas_rechazadas_si_finalizado(self):
        crear = self.client.post(
            reverse('stats_app:api_crear_nota', args=[self.partido.id]),
            data=json.dumps({'texto': 'Nueva', 'set_numero': 1}),
            content_type='application/json',
        )
        self.assertEqual(crear.status_code, 400)

        actualizar = self.client.post(
            reverse('stats_app:api_actualizar_nota', args=[self.partido.id, self.nota.id]),
            data=json.dumps({'texto': 'Editada', 'set_numero': 1}),
            content_type='application/json',
        )
        self.assertEqual(actualizar.status_code, 400)

        eliminar = self.client.post(
            reverse('stats_app:api_eliminar_nota', args=[self.partido.id, self.nota.id]),
        )
        self.assertEqual(eliminar.status_code, 400)
        self.assertTrue(NotaPartido.objects.filter(pk=self.nota.pk).exists())

    def test_reabrir_permite_registrar_de_nuevo(self):
        reopen = self.client.post(reverse('stats_app:api_reabrir_partido', args=[self.partido.id]))
        self.assertEqual(reopen.status_code, 200)
        response = self.client.post(
            reverse('stats_app:api_registrar_estadistica'),
            data=json.dumps({
                'partido_id': self.partido.id, 'jugadora_id': self.jugadora.id,
                'accion': 'SAQUE', 'calidad': '+', 'fase': 'K0', 'set_numero': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RegistroEstadistica.objects.filter(partido=self.partido).count(), 2)

    @patch('stats_app.views.informes.render_to_pdf', return_value=None)
    def test_pdf_resumen_devuelve_500_si_render_falla(self, _mock_pdf):
        response = self.client.get(reverse('stats_app:descargar_resumen_pdf', args=[self.partido.pk]))
        self.assertEqual(response.status_code, 500)


class BlindajePerimetroTests(TestCase):
    """CSP, registro cerrado, rate limit de reset, telemetría de cliente y admin path."""

    def setUp(self):
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido('coach_perimetro')
        self.client.login(username='coach_perimetro', password='pass12345')

    def test_respuestas_incluyen_cabeceras_de_seguridad(self):
        response = self.client.get(reverse('stats_app:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.get('Content-Security-Policy', ''))
        self.assertNotIn('unpkg.com', response.get('Content-Security-Policy', ''))
        self.assertEqual(response.get('Referrer-Policy'), 'strict-origin-when-cross-origin')
        self.assertIn('camera=()', response.get('Permissions-Policy', ''))

    @override_settings(ALLOW_PUBLIC_REGISTRATION=False)
    def test_registro_publico_desactivado_da_404(self):
        anon = Client()
        response = anon.get(reverse('register'))
        self.assertEqual(response.status_code, 404)

    def test_password_reset_esta_en_rate_limit_rules(self):
        patterns = [p for p, _, _ in settings.RATE_LIMIT_RULES]
        self.assertTrue(any('password_reset' in p for p in patterns))

    def test_client_error_api_registra_y_responde_ok(self):
        response = self.client.post(
            reverse('stats_app:api_client_error'),
            data=json.dumps({
                'mensaje': 'boom test',
                'origen': 'test',
                'partido_id': self.partido.id,
                'stack': 'Error: boom',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    @override_settings(DJANGO_ADMIN_URL='mvs-test-admin')
    def test_admin_url_setting_configurable(self):
        self.assertEqual(settings.DJANGO_ADMIN_URL, 'mvs-test-admin')


class PlantillaCSVParserTests(TestCase):
    """Parser de plantilla: Excel ES, BOM, abreviaturas y filas inválidas."""

    def test_normaliza_abreviaturas_de_posicion(self):
        self.assertEqual(normalizar_posicion('Co'), 'COLOCADORA')
        self.assertEqual(normalizar_posicion(' col '), 'COLOCADORA')
        self.assertEqual(normalizar_posicion('R'), 'RECEPTORA')
        self.assertEqual(normalizar_posicion('REC'), 'RECEPTORA')
        self.assertEqual(normalizar_posicion('C'), 'CENTRAL')
        self.assertEqual(normalizar_posicion('O'), 'OPUESTA')
        self.assertEqual(normalizar_posicion('L'), 'LIBERO')
        self.assertEqual(normalizar_posicion('COLOCADORA'), 'COLOCADORA')
        self.assertIsNone(normalizar_posicion('XX'))

    def test_parsea_punto_y_coma_tildes_y_bom(self):
        texto = 'dorsal;nombre;apellidos;posicion\n4;Laura;Gómez;R\n'
        contenido = texto.encode('utf-8-sig')
        parseo = parsear_plantilla_csv(contenido)
        self.assertEqual(parseo.errores, [])
        self.assertEqual(len(parseo.filas), 1)
        self.assertEqual(parseo.filas[0].nombre, 'Laura')
        self.assertEqual(parseo.filas[0].apellidos, 'Gómez')
        self.assertEqual(parseo.filas[0].posicion, 'RECEPTORA')

    def test_fila_invalida_no_impide_las_validas(self):
        csv_txt = (
            'dorsal,nombre,apellidos,posicion\n'
            '7,Marta,Sanz,Co\n'
            ',Sin,Dorsal,C\n'
            '7,Otra,Marta,R\n'
            '8,Paula,Navarro,XX\n'
            '9,Irene,Vega,CEN\n'
        )
        parseo = parsear_plantilla_csv(csv_txt.encode('utf-8'))
        dorsales = [f.dorsal for f in parseo.filas]
        self.assertEqual(dorsales, [7, None, 9])
        self.assertTrue(any('duplicado' in e for e in parseo.errores))
        self.assertTrue(any('no válida' in e for e in parseo.errores))

    def test_parsea_informe_club_nombre_apellido_sin_dorsal(self):
        csv_txt = (
            'Categoría y Equipo,Nombre,Apellido,Año,Dorsal,Técnica\n'
            ',,,,,,,,\n'
            'INFANTIL A,María,Callesi Flor,2012,,7\n'
            'INFANTIL A,Carola,Delgado Moreno,2013,,9\n'
        )
        parseo = parsear_plantilla_csv(csv_txt.encode('utf-8'))
        self.assertEqual(parseo.errores, [])
        self.assertEqual(len(parseo.filas), 2)
        self.assertEqual(parseo.filas[0].nombre, 'María')
        self.assertEqual(parseo.filas[0].apellidos, 'Callesi Flor')
        self.assertIsNone(parseo.filas[0].dorsal)
        self.assertEqual(parseo.filas[0].fecha_nacimiento.year, 2012)
        self.assertEqual(parseo.sin_dorsal, 2)

    def test_parsea_asistencia_con_logo_y_n_dorsal(self):
        csv_txt = (
            'Claret,,,,\n'
            ',,,,\n'
            'Nº,Talla,compit,Apellidos y Nombre,,Fecha nac\n'
            '18,,SI,Lucía,García Porfirio,14/1/2014\n'
            '25,,SI,Belén,Alcalde Guerrero,1/2/2014\n'
        )
        parseo = parsear_plantilla_csv(csv_txt.encode('utf-8'))
        self.assertEqual(parseo.errores, [])
        self.assertEqual(len(parseo.filas), 2)
        self.assertEqual(parseo.filas[0].dorsal, 18)
        self.assertEqual(parseo.filas[0].nombre, 'Lucía')
        self.assertEqual(parseo.filas[0].apellidos, 'García Porfirio')


class PlantillaCSVViewsTests(TestCase):
    """Exportar/importar CSV en Gestión de Equipos."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, self.jugadora, _ = _crear_entrenador_con_partido('coach_csv')
        self.client.login(username='coach_csv', password='pass12345')

    def test_exportar_csv_incluye_cabecera_y_jugadora(self):
        response = self.client.get(reverse('stats_app:equipo_exportar_csv', args=[self.equipo.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        cuerpo = response.content.decode('utf-8-sig')
        self.assertTrue(cuerpo.startswith('dorsal,nombre,apellidos,posicion'))
        self.assertIn('7,Val,Con,OPUESTA', cuerpo)

    def test_importar_crea_y_actualiza_sin_borrar_al_resto(self):
        otra = Jugadora.objects.create(
            equipo=self.equipo, nombre='Sara', apellidos='Ortiz', dorsal=12, posicion='RECEPTORA',
        )
        csv_txt = (
            'dorsal,nombre,apellidos,posicion\n'
            '7,Marta,Sanz,Co\n'
            '4,Laura,Gómez,R\n'
        )
        response = self.client.post(
            reverse('stats_app:equipo_importar_csv', args=[self.equipo.pk]),
            data={'archivo': SimpleUploadedFile('plantilla.csv', csv_txt.encode('utf-8-sig'), content_type='text/csv')},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.jugadora.refresh_from_db()
        self.assertEqual(self.jugadora.nombre, 'Marta')
        self.assertEqual(self.jugadora.posicion, 'COLOCADORA')
        nueva = Jugadora.objects.get(equipo=self.equipo, dorsal=4)
        self.assertEqual(nueva.nombre, 'Laura')
        otra.refresh_from_db()
        self.assertEqual(otra.nombre, 'Sara')
        self.assertContains(response, '1 creadas')
        self.assertContains(response, '1 actualizadas')

    def test_importar_informe_club_crea_sin_dorsal_y_no_duplica(self):
        csv_txt = (
            'Categoría y Equipo,Nombre,Apellido,Año,Dorsal,Técnica\n'
            ',,,,,,,,\n'
            'INFANTIL A,María,Callesi Flor,2012,,7\n'
            'INFANTIL A,Carola,Delgado Moreno,2013,,9\n'
        )
        url = reverse('stats_app:equipo_importar_csv', args=[self.equipo.pk])
        archivo = lambda: SimpleUploadedFile(
            'informe.csv', csv_txt.encode('utf-8'), content_type='text/csv',
        )
        primero = self.client.post(url, data={'archivo': archivo()}, follow=True)
        self.assertEqual(primero.status_code, 200)
        self.assertEqual(
            Jugadora.objects.filter(equipo=self.equipo, nombre='María', apellidos='Callesi Flor').count(),
            1,
        )
        maria = Jugadora.objects.get(equipo=self.equipo, nombre='María', apellidos='Callesi Flor')
        self.assertIsNone(maria.dorsal)
        self.assertEqual(maria.fecha_nacimiento.year, 2012)
        segundo = self.client.post(url, data={'archivo': archivo()}, follow=True)
        self.assertEqual(segundo.status_code, 200)
        self.assertEqual(
            Jugadora.objects.filter(equipo=self.equipo, nombre='María', apellidos='Callesi Flor').count(),
            1,
        )


def _accion_scout(partido, jugadora, accion, calidad, fase='K1', set_numero=1):
    return RegistroEstadistica.objects.create(
        partido=partido, jugadora=jugadora, tipo_fase=fase,
        accion=accion, calidad=calidad, set_numero=set_numero,
    )


class FichaTemporadaTests(TestCase):
    """Sprint C: ficha de jugadora, agregación y KPIs de Inicio."""

    def setUp(self):
        cache.clear()
        self.coach, self.equipo, self.jugadora, self.partido = _crear_entrenador_con_partido(
            'coach_ficha'
        )
        self.client.login(username='coach_ficha', password='pass12345')
        self.sin_scout = Jugadora.objects.create(
            equipo=self.equipo, nombre='Laura', apellidos='SinDatos',
        )

    def test_ficha_vacia_sin_scout_y_sin_dorsal(self):
        response = self.client.get(reverse('stats_app:jugadora_ficha', args=[self.sin_scout.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['temporada'].totales)
        self.assertContains(response, 'Sin scout en esta temporada')
        self.assertContains(response, 'Sin posición')
        self.assertContains(response, 'Falta el dorsal y la posición')
        self.assertNotContains(response, 'Recepción +')

    def test_agrega_varios_partidos_finalizados_y_ignora_el_abierto(self):
        self.partido.finalizado = True
        self.partido.rival = 'Rival Uno'
        self.partido.fecha = date(2026, 1, 10)
        self.partido.save()
        _accion_scout(self.partido, self.jugadora, 'ATAQUE', '++')
        _accion_scout(self.partido, self.jugadora, 'ATAQUE', '++')
        _accion_scout(self.partido, self.jugadora, 'RECEPCION', '+')
        _accion_scout(self.partido, self.jugadora, 'SAQUE', '++')

        segundo = Partido.objects.create(
            equipo=self.equipo, fecha=date(2026, 1, 17), hora=time(18, 0),
            rival='Rival Dos', local=True, lugar='Pabellón', finalizado=True,
        )
        _accion_scout(segundo, self.jugadora, 'ATAQUE', '++')
        _accion_scout(segundo, self.jugadora, 'BLOQUEO', '++')
        _accion_scout(segundo, self.jugadora, 'RECEPCION', '--')

        abierto = Partido.objects.create(
            equipo=self.equipo, fecha=date(2026, 2, 1), hora=time(18, 0),
            rival='No cuenta', local=True, lugar='Pabellón',
        )
        _accion_scout(abierto, self.jugadora, 'ATAQUE', '++')

        stats = stats_jugadora_temporada(self.jugadora)
        self.assertEqual(stats.partidos_count, 2)
        self.assertEqual(stats.totales['partidos'], 2)
        self.assertEqual(stats.totales['ataque'], 3)
        self.assertEqual(stats.totales['bloqueo'], 1)
        self.assertEqual(stats.totales['saque'], 1)
        self.assertEqual(stats.totales['recepcion_pct'], 50.0)
        self.assertEqual(stats.totales['balance'], 4)
        self.assertEqual([f.partido.rival for f in stats.partidos], ['Rival Uno', 'Rival Dos'])

        response = self.client.get(reverse('stats_app:jugadora_ficha', args=[self.jugadora.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rival Uno')
        self.assertContains(response, 'Rival Dos')
        self.assertNotContains(response, 'No cuenta')
        self.assertContains(response, 'Opuesta')

        listado = self.client.get(reverse('stats_app:equipos_list'))
        self.assertContains(listado, reverse('stats_app:jugadora_ficha', args=[self.jugadora.pk]))
        self.assertContains(listado, reverse('stats_app:jugadora_editar', args=[self.jugadora.pk]))

    def test_kpis_en_pizarra_no_en_inicio(self):
        hoy = timezone.localdate()
        self.partido.fecha = hoy
        self.partido.save()

        sin_historial = self.client.get(reverse('stats_app:dashboard'))
        self.assertNotIn('kpis_temporada', sin_historial.context)
        self.assertNotContains(sin_historial, 'Puntos al recibir')

        cerrado = Partido.objects.create(
            equipo=self.equipo, fecha=hoy - timedelta(days=7), hora=time(18, 0),
            rival='Ya jugado', local=True, lugar='Pabellón', finalizado=True,
        )
        _puntos_set(cerrado, self.jugadora, 1, 25, 18)
        _accion_scout(cerrado, self.jugadora, 'SAQUE', '++', fase='K0')
        _accion_scout(cerrado, self.jugadora, 'SAQUE', '--', fase='K0')

        inicio = self.client.get(reverse('stats_app:dashboard'))
        self.assertNotContains(inicio, 'Puntos al recibir')
        self.assertContains(inicio, 'Preparar partido')

        pizarra = self.client.get(
            reverse('stats_app:modo_partido', args=[self.partido.pk]),
            {'tab': 'rotacion'},
        )
        kpis = pizarra.context['kpis_temporada']
        self.assertIsNotNone(kpis)
        self.assertEqual(kpis.partidos, 1)
        self.assertEqual(kpis.victorias, 1)
        self.assertEqual(kpis.derrotas, 0)
        self.assertEqual(kpis.sets_local, 1)
        self.assertEqual(kpis.sets_rival, 0)
        self.assertIsNotNone(kpis.sideout_pct)
        self.assertIsNotNone(kpis.breakpoint_pct)
        self.assertIsNotNone(kpis.ataque_pct)
        self.assertContains(pizarra, 'Puntos al recibir')
        self.assertContains(pizarra, 'Puntos al defender')
        self.assertContains(pizarra, '1V – 0D')

        equipo_kpis = stats_temporada_equipo(self.equipo)
        self.assertEqual(equipo_kpis.partidos, 1)
        self.assertEqual(equipo_kpis.victorias, 1)
        self.assertEqual(equipo_kpis.breakpoint_pct, 50.0)

