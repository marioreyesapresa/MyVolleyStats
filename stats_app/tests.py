"""Pruebas de aislamiento multi-entrenador.

Estas pruebas usan la base de datos de pruebas que Django crea y destruye
automáticamente (nunca tocan db.sqlite3). Ejecutar con:

    python manage.py test stats_app
"""
import json
import threading
from datetime import date, time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .db_utils import reintentar_en_error_transitorio
from .forms import RegistrarAccionForm, RegistrarCambioForm, EliminarAccionForm
from .models import Equipo, Jugadora, Partido, RegistroEstadistica, RotacionSet
from .security import RateLimitMiddleware

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
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='otro').exists())


# ═════════════════════════════════════════════════════════════════════════════
# TESTS DE INTRUSIÓN Y SEGURIDAD
#
# Simulan ataques reales contra "Scout Rotation Pro" para verificar que las
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
        # los cálculos de eficacia, MVP, alertas y K1/K2 de reporting.py.
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
        self.assertIn('mvp', data)
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
        nueva = Jugadora.objects.get(nombre='Nueva')

        editar = self.client.post(reverse('stats_app:jugadora_editar', args=[nueva.pk]), data={
            'equipo': self.equipo.id, 'nombre': 'Editada', 'apellidos': 'Jugadora',
            'dorsal': 99, 'posicion': 'OPUESTA', 'fecha_nacimiento': '2006-05-05',
        })
        self.assertEqual(editar.status_code, 302)
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
