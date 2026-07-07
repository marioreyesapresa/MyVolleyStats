"""Capturas del manual de usuario: carga desde estáticos para el PDF."""

import base64
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders

MANUAL_IMAGE_NAMES = (
    '01_dashboard.png',
    '02_equipos.png',
    '03_configuracion.png',
    '04_rotacion.png',
    '05_scout_rapido.png',
    '06_scout_avanzado.png',
    '07_indicadores.png',
    '08_archivos.png',
    '09_estadisticas.png',
    '10_marcador.png',
    '11_acciones.png',
    '12_ajustes.png',
    '13_estadisticas_avanzado.png',
)

MANUAL_IMAGE_KEYS = {
    '01_dashboard.png': 'dashboard',
    '02_equipos.png': 'equipos',
    '03_configuracion.png': 'configuracion',
    '04_rotacion.png': 'rotacion',
    '05_scout_rapido.png': 'scout_rapido',
    '06_scout_avanzado.png': 'scout_avanzado',
    '07_indicadores.png': 'indicadores',
    '08_archivos.png': 'archivos',
    '09_estadisticas.png': 'estadisticas',
    '10_marcador.png': 'marcador',
    '11_acciones.png': 'acciones',
    '12_ajustes.png': 'ajustes',
    '13_estadisticas_avanzado.png': 'estadisticas_avanzado',
}


def manual_images_dir():
    return Path(settings.BASE_DIR) / 'stats_app' / 'static' / 'stats_app' / 'manual'


def _find_manual_image(filename):
    rel = f'stats_app/manual/{filename}'
    path = finders.find(rel)
    if path:
        return Path(path)
    fallback = manual_images_dir() / filename
    return fallback if fallback.is_file() else None


def image_to_data_uri(path):
    path = Path(path)
    if not path.is_file():
        return None
    data = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'data:image/png;base64,{data}'


def manual_logo_uri():
    path = finders.find('stats_app/icons/icon-192.png')
    if not path:
        fallback = Path(settings.BASE_DIR) / 'stats_app/static/stats_app/icons/icon-192.png'
        path = fallback if fallback.is_file() else None
    return image_to_data_uri(path) if path else None


def build_manual_capturas_context():
    """Devuelve dict clave → data URI para incrustar en el PDF del manual."""
    capturas = {}
    for filename in MANUAL_IMAGE_NAMES:
        path = _find_manual_image(filename)
        if not path:
            continue
        key = MANUAL_IMAGE_KEYS[filename]
        uri = image_to_data_uri(path)
        if uri:
            capturas[key] = uri
    return capturas
