from django.conf import settings
from django.shortcuts import render


def handler500(request):
    """Página 500 personalizada: el mensaje de notificación al equipo técnico
    solo aparece si ADMIN_NOTIFY_EMAIL está configurado (ADMINS en settings)."""
    return render(
        request,
        '500.html',
        {'notificacion_activa': bool(getattr(settings, 'ADMINS', []))},
        status=500,
    )
