"""Pruebas de aislamiento multi-entrenador.

Estas pruebas usan la base de datos de pruebas que Django crea y destruye
automáticamente (nunca tocan db.sqlite3). Ejecutar con:

    python manage.py test stats_app
"""
import json
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Equipo, Jugadora, Partido

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
