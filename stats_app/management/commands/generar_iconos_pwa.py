"""Genera los iconos de la PWA (manifest.json + apple-touch-icon) a partir
del logo real proporcionado (balón de voleibol "line art" sobre fondo azul
noche), en vez de dibujarlo programáticamente con Pillow.

El PNG original vive en `stats_app/static/stats_app/source/logo_balon_voley.png`
(committeado en el repo para que la generación sea determinista y no
dependa de ningún fichero externo al proyecto). Este comando:

    1. Localiza automáticamente el balón dentro de la imagen fuente
       (detectando el área que difiere del color de fondo de la esquina),
       para poder recortarlo centrado con un margen proporcional aunque la
       imagen original cambie de tamaño.
    2. Recorta un cuadrado centrado en el balón con un pequeño margen.
    3. Reescala con LANCZOS a cada tamaño de salida.

Uso:

    python manage.py generar_iconos_pwa

Sobrescribe (siempre determinista, sin llamadas de red) los ficheros en
`stats_app/static/stats_app/icons/`:

    - icon-192.png            (Android/manifest)
    - icon-512.png            (Android/manifest)
    - apple-touch-icon.png    (180x180, iOS — SIN transparencia, obligatorio)
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageChops

SOURCE_PATH = (
    Path(settings.BASE_DIR) / 'stats_app' / 'static' / 'stats_app' / 'source' / 'logo_balon_voley.png'
)
OUT_DIR = Path(settings.BASE_DIR) / 'stats_app' / 'static' / 'stats_app' / 'icons'

MARGEN = 0.08  # margen proporcional alrededor del balón detectado


def _recorte_centrado_en_el_balon(img, margen=MARGEN):
    """Detecta el balón (todo lo que difiera del color de fondo de la
    esquina superior izquierda) y devuelve un recorte cuadrado, centrado en
    él, con un margen proporcional a su tamaño."""
    color_fondo = img.getpixel((0, 0))
    fondo_plano = Image.new(img.mode, img.size, color_fondo)
    diff = ImageChops.difference(img, fondo_plano).convert('L')
    # Umbral para ignorar el ligero degradado/viñeta del fondo y quedarnos
    # solo con los trazos reales del balón.
    mascara = diff.point(lambda p: 255 if p > 60 else 0)
    bbox = mascara.getbbox()
    if bbox is None:
        raise CommandError(f'No se detectó ningún contenido en {SOURCE_PATH}')

    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lado_contenido = max(x1 - x0, y1 - y0)
    lado_con_margen = lado_contenido * (1 + margen * 2)
    mitad = lado_con_margen / 2

    left = max(0, cx - mitad)
    top = max(0, cy - mitad)
    right = min(img.width, cx + mitad)
    bottom = min(img.height, cy + mitad)
    # Recuadro final cuadrado (por si el clamp contra los bordes rompió la
    # proporción): usamos el lado más pequeño para no colarnos del lienzo.
    lado_final = min(right - left, bottom - top)
    left = cx - lado_final / 2
    top = cy - lado_final / 2

    return img.crop((round(left), round(top), round(left + lado_final), round(top + lado_final)))


def generar_icono(size):
    """Carga el logo fuente, lo recorta centrado en el balón y lo reescala
    a un cuadrado de `size`x`size` px con interpolación de alta calidad."""
    with Image.open(SOURCE_PATH) as img:
        img = img.convert('RGB')
        recorte = _recorte_centrado_en_el_balon(img)
        return recorte.resize((size, size), Image.Resampling.LANCZOS)


class Command(BaseCommand):
    help = 'Genera/actualiza los iconos de la PWA a partir del logo real (manifest + apple-touch-icon).'

    def handle(self, *args, **options):
        if not SOURCE_PATH.exists():
            raise CommandError(f'No se encuentra el logo fuente: {SOURCE_PATH}')

        OUT_DIR.mkdir(parents=True, exist_ok=True)

        for size in (192, 512):
            icono = generar_icono(size)
            destino = OUT_DIR / f'icon-{size}.png'
            icono.save(destino, 'PNG')
            self.stdout.write(self.style.SUCCESS(f'  ✓ {destino}'))

        # apple-touch-icon: 180x180, iOS exige que NO tenga canal alfa.
        icono_apple = generar_icono(180)
        destino_apple = OUT_DIR / 'apple-touch-icon.png'
        icono_apple.save(destino_apple, 'PNG')  # ya es RGB (sin alfa)
        self.stdout.write(self.style.SUCCESS(f'  ✓ {destino_apple} (sin transparencia)'))

        self.stdout.write(self.style.SUCCESS('Iconos PWA generados correctamente a partir del logo real.'))
