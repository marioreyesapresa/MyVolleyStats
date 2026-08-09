"""Constantes de marca disponibles en todas las plantillas."""

APP_NAME = 'MyVolleyStats'
APP_TAGLINE = 'Estadísticas de Voleibol y Scout Táctico'
APP_FULL_TAGLINE = f'{APP_NAME} — {APP_TAGLINE}'
# Incrementar al regenerar iconos para invalidar caché de favicon en navegadores.
APP_ICON_VERSION = '2'


def branding(request):
    from django.conf import settings
    return {
        'APP_NAME': APP_NAME,
        'APP_TAGLINE': APP_TAGLINE,
        'APP_FULL_TAGLINE': APP_FULL_TAGLINE,
        'APP_ICON_VERSION': APP_ICON_VERSION,
        'ALLOW_PUBLIC_REGISTRATION': getattr(settings, 'ALLOW_PUBLIC_REGISTRATION', True),
        'CONTACT_EMAIL': getattr(settings, 'CONTACT_EMAIL', 'myvolleystats@gmail.com'),
    }
