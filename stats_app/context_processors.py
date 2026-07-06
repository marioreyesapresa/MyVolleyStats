"""Constantes de marca disponibles en todas las plantillas."""

APP_NAME = 'MyVolleyStats'
APP_TAGLINE = 'Estadísticas de Voleibol y Scout Táctico'
APP_FULL_TAGLINE = f'{APP_NAME} — {APP_TAGLINE}'


def branding(request):
    return {
        'APP_NAME': APP_NAME,
        'APP_TAGLINE': APP_TAGLINE,
        'APP_FULL_TAGLINE': APP_FULL_TAGLINE,
    }
