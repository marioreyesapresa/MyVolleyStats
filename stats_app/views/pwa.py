"""Vistas de soporte para la PWA (Progressive Web App).

El Service Worker debe servirse desde la RAÍZ del sitio (`/service-worker.js`),
no desde `/static/...`. El alcance (scope) por defecto de un Service Worker
es el directorio donde vive su URL: si se sirve desde `/static/stats_app/`,
solo podría controlar páginas bajo esa ruta y jamás las pantallas reales de
la app (`/dashboard/`, `/partido/<id>/modo-partido/`, etc.). Aunque existe
la cabecera `Service-Worker-Allowed` para ampliar el scope manualmente, su
soporte en Safari/iOS —el objetivo principal de esta PWA— es poco fiable.

Esta vista lee el fichero real desde el directorio de estáticos de la app
(la misma fuente que edita el equipo de frontend) y lo sirve como si
estuviese en la raíz, sin depender de que `collectstatic` se haya ejecutado
ni de la configuración de storage/hashing de WhiteNoise.
"""
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

_SERVICE_WORKER_PATH = Path(settings.BASE_DIR) / 'stats_app' / 'static' / 'stats_app' / 'service-worker.js'


@require_GET
@never_cache
def service_worker_view(request):
    """Sirve `service-worker.js` en la raíz del dominio con scope completo ('/')."""
    contenido = _SERVICE_WORKER_PATH.read_text(encoding='utf-8')
    response = HttpResponse(contenido, content_type='text/javascript')
    # Amplía explícitamente el scope por si el navegador lo respeta (Chrome/
    # Firefox); en Safari/iOS el scope ya es correcto por servirse desde "/".
    response['Service-Worker-Allowed'] = '/'
    return response
