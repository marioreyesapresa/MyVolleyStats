"""Genera los iconos de la PWA y el logo para la UI web.

El PNG fuente vive en `stats_app/static/stats_app/source/logo_balon_voley.png`.

Elimina el margen blanco/gris exterior del icono y genera:

    - icon-192.png / icon-512.png   (PWA, fondo #0b0f19)
    - apple-touch-icon.png          (180×180, sin transparencia)
    - logo-ui.png                   (256×256, fondo transparente — login, menú)

Uso:

    python manage.py generar_iconos_pwa
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

SOURCE_PATH = (
    Path(settings.BASE_DIR) / 'stats_app' / 'static' / 'stats_app' / 'source' / 'logo_balon_voley.png'
)
OUT_DIR = Path(settings.BASE_DIR) / 'stats_app' / 'static' / 'stats_app' / 'icons'
BG_APP = (11, 15, 25)  # #0b0f19
UMBRAL_FONDO_CLARO = 200


def _quitar_margen_claro(img):
    """Recorta el marco blanco/gris y deja solo el icono oscuro."""
    rgba = img.convert('RGBA')
    gray = rgba.convert('L')
    bbox = gray.point(lambda p: 255 if p < UMBRAL_FONDO_CLARO else 0).getbbox()
    if bbox is None:
        raise CommandError(f'No se detectó contenido en {SOURCE_PATH}')
    return rgba.crop(bbox)


def _hacer_cuadrado(img):
    lado = max(img.width, img.height)
    cuadrado = Image.new('RGBA', (lado, lado), (0, 0, 0, 0))
    offset = ((lado - img.width) // 2, (lado - img.height) // 2)
    cuadrado.paste(img, offset)
    return cuadrado


def _sobre_fondo_app(img_rgba, size):
    """Icono PWA: escala y aplana sobre el color de fondo de la app."""
    escala = img_rgba.resize((size, size), Image.Resampling.LANCZOS)
    fondo = Image.new('RGB', (size, size), BG_APP)
    fondo.paste(escala, mask=escala.split()[3])
    return fondo


def _logo_ui_transparente(img_rgba, size):
    """Logo web: escala con canal alfa para fondos oscuros."""
    return img_rgba.resize((size, size), Image.Resampling.LANCZOS)


def _cargar_logo_limpio():
    with Image.open(SOURCE_PATH) as img:
        recorte = _quitar_margen_claro(img)
        return _hacer_cuadrado(recorte)


class Command(BaseCommand):
    help = 'Genera iconos PWA y logo UI sin margen blanco exterior.'

    def handle(self, *args, **options):
        if not SOURCE_PATH.exists():
            raise CommandError(f'No se encuentra el logo fuente: {SOURCE_PATH}')

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        limpio = _cargar_logo_limpio()

        for size in (192, 512):
            icono = _sobre_fondo_app(limpio, size)
            destino = OUT_DIR / f'icon-{size}.png'
            icono.save(destino, 'PNG')
            self.stdout.write(self.style.SUCCESS(f'  ✓ {destino}'))

        icono_apple = _sobre_fondo_app(limpio, 180)
        destino_apple = OUT_DIR / 'apple-touch-icon.png'
        icono_apple.save(destino_apple, 'PNG')
        self.stdout.write(self.style.SUCCESS(f'  ✓ {destino_apple}'))

        logo_ui = _logo_ui_transparente(limpio, 256)
        destino_ui = OUT_DIR / 'logo-ui.png'
        logo_ui.save(destino_ui, 'PNG')
        self.stdout.write(self.style.SUCCESS(f'  ✓ {destino_ui} (transparente)'))

        self.stdout.write(self.style.SUCCESS('Iconos generados correctamente.'))
