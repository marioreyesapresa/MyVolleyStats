"""Genera capturas de pantalla para el manual de usuario (PDF).

Requiere Playwright instalado:
    pip install playwright && playwright install chromium

Uso:
    python manage.py generar_capturas_manual
"""
import os
import signal
import subprocess
import sys
import time
from datetime import date, time as dt_time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stats_app.models import Equipo, Jugadora, Partido, RegistroEstadistica, RotacionSet
from stats_app.services.manual_images import manual_images_dir

MANUAL_USER = 'manual_capturas'
MANUAL_PASS = 'manual_capturas_dev'
EQUIPO_NOMBRE = '[MANUAL] Equipo Demo'
RIVAL = '[MANUAL] Rival Demo'
PORT = 18765
BASE_URL = f'http://127.0.0.1:{PORT}'


class Command(BaseCommand):
    help = 'Genera capturas PNG de la app para el manual de usuario (requiere Playwright).'

    def handle(self, *args, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CommandError(
                'Instala Playwright: pip install playwright && playwright install chromium'
            ) from exc

        out_dir = manual_images_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        partido = self._asegurar_datos_demo()
        server = self._iniciar_servidor()
        try:
            time.sleep(2)
            self._capturar(sync_playwright, partido.id, out_dir)
        finally:
            self._parar_servidor(server)

        self.stdout.write(self.style.SUCCESS(f'Capturas guardadas en {out_dir}'))

    def _asegurar_datos_demo(self):
        User = get_user_model()
        with transaction.atomic():
            user, _ = User.objects.get_or_create(
                username=MANUAL_USER,
                defaults={'email': 'manual@local.dev'},
            )
            user.set_password(MANUAL_PASS)
            user.save()

            equipo, _ = Equipo.objects.get_or_create(
                nombre=EQUIPO_NOMBRE,
                temporada='2025/2026',
                defaults={
                    'entrenador': user,
                    'categoria': 'SENIOR',
                },
            )
            if equipo.entrenador_id != user.id:
                equipo.entrenador = user
                equipo.save(update_fields=['entrenador'])

            jugadoras = []
            posiciones = ['COLOCADORA', 'OPUESTA', 'CENTRAL', 'RECEPTORA', 'RECEPTORA', 'CENTRAL', 'LIBERO']
            for i, pos in enumerate(posiciones, start=1):
                j, _ = Jugadora.objects.get_or_create(
                    equipo=equipo,
                    dorsal=i,
                    defaults={
                        'nombre': f'Jugadora{i}',
                        'apellidos': 'Demo',
                        'posicion': pos,
                        'fecha_nacimiento': date(2008, 1, 15),
                    },
                )
                jugadoras.append(j)

            partido, _ = Partido.objects.get_or_create(
                equipo=equipo,
                rival=RIVAL,
                fecha=date.today(),
                defaults={
                    'hora': dt_time(18, 0),
                    'local': True,
                    'lugar': 'Pabellón Demo',
                    'modalidad': 'VOLEY',
                },
            )
            partido.finalizado = False
            partido.save(update_fields=['finalizado'])

            RotacionSet.objects.filter(partido=partido, set_numero=1).delete()
            RotacionSet.objects.create(
                partido=partido,
                set_numero=1,
                es_inicial=True,
                pos1=jugadoras[0],
                pos2=jugadoras[1],
                pos3=jugadoras[2],
                pos4=jugadoras[3],
                pos5=jugadoras[4],
                pos6=jugadoras[5],
                libero1=jugadoras[6],
            )

            RegistroEstadistica.objects.filter(partido=partido).delete()
            muestras = [
                ('SAQUE', '++', jugadoras[0], 'K0'),
                ('RECEPCION', '+', jugadoras[3], 'K1'),
                ('ATAQUE', '++', jugadoras[1], 'K1'),
                ('BLOQUEO', '+', jugadoras[2], 'K2'),
                ('DEFENSA', '=', jugadoras[4], 'K2'),
                ('ERROR_RIVAL', '', None, 'K1'),
            ]
            for accion, calidad, jugadora, fase in muestras:
                RegistroEstadistica.objects.create(
                    partido=partido,
                    jugadora=jugadora,
                    tipo_fase=fase,
                    accion=accion,
                    calidad=calidad,
                    set_numero=1,
                )

        return partido

    def _iniciar_servidor(self):
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': settings.SETTINGS_MODULE}
        proc = subprocess.Popen(
            [sys.executable, 'manage.py', 'runserver', str(PORT), '--noreload'],
            cwd=settings.BASE_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc

    def _parar_servidor(self, proc):
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _prepare_scout_ui(self, page, modo):
        """Asegura modo visible y sin overlays antes de capturar el scout."""
        page.evaluate("""
            () => {
                if (typeof unlockGameplayUI === 'function') unlockGameplayUI();
                const modal = document.getElementById('modal-fin-set');
                if (modal) modal.classList.add('hidden');
                if (typeof state !== 'undefined') {
                    state.set_finished = false;
                    state.set_modal_suppressed = true;
                }
            }
        """)
        if modo == 'avanzado':
            page.evaluate(
                "() => { if (typeof cambiarModoScout === 'function') cambiarModoScout('avanzado'); }"
            )
        else:
            # Marcador y Acciones viven dentro del contenedor Rápido: si la sesión
            # quedó en Avanzado (captura anterior), la pantalla sale negra.
            page.evaluate(
                "() => { if (typeof cambiarModoScout === 'function') cambiarModoScout('rapido'); }"
            )
        page.wait_for_timeout(800)

        tab_by_modo = {
            'rapido': 'gameplay',
            'marcador': 'score',
            'acciones': 'acciones',
            'archivos': 'file',
            'ajustes': 'adjust',
        }
        if modo in tab_by_modo:
            tab = tab_by_modo[modo]
            page.evaluate(
                f"() => {{ if (typeof switchRapidoTab === 'function') switchRapidoTab('{tab}'); }}"
            )
        page.wait_for_timeout(1500)
        if modo == 'acciones':
            try:
                page.wait_for_selector('#quick-history-list > div', timeout=8000)
            except Exception:
                pass

    def _capturar(self, sync_playwright, partido_id, out_dir):
        shots = [
            ('01_dashboard.png', f'{BASE_URL}/', None),
            ('02_equipos.png', f'{BASE_URL}/equipos/', None),
            ('03_configuracion.png', f'{BASE_URL}/configuracion/', None),
            ('04_rotacion.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=rotacion', None),
            ('05_scout_rapido.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=scout', 'rapido'),
            ('06_scout_avanzado.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=scout', 'avanzado'),
            ('07_indicadores.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=metricas', None),
            ('08_archivos.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=scout', 'archivos'),
            ('09_estadisticas.png', f'{BASE_URL}/partido/{partido_id}/stats-final/', None),
            ('10_marcador.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=scout', 'marcador'),
            ('11_acciones.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=scout', 'acciones'),
            ('12_ajustes.png', f'{BASE_URL}/partido/{partido_id}/modo-partido/?tab=scout', 'ajustes'),
            ('13_estadisticas_avanzado.png', f'{BASE_URL}/partido/{partido_id}/stats-avanzado/', None),
        ]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 800})
            page.set_default_timeout(30000)

            page.goto(f'{BASE_URL}/accounts/login/')
            page.fill('#id_username', MANUAL_USER)
            page.fill('#id_password', MANUAL_PASS)
            page.click('button[type="submit"]')
            page.wait_for_url('**/')

            for filename, url, modo in shots:
                page.goto(url)
                page.wait_for_load_state('networkidle')
                page.wait_for_timeout(1200)

                if modo:
                    self._prepare_scout_ui(page, modo)

                dest = Path(out_dir) / filename
                page.screenshot(path=str(dest), full_page=False)
                self.stdout.write(f'  ✓ {filename}')

            browser.close()
