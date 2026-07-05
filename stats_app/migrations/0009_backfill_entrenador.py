from django.conf import settings
from django.db import migrations


def asignar_entrenador_por_defecto(apps, schema_editor):
    """Asigna los equipos existentes (creados antes del multi-tenant) a un
    usuario administrador por defecto, para poder aplicar después la
    restricción NOT NULL sin perder datos.

    IMPORTANTE: tras desplegar, revisa en /admin/ que cada equipo quede
    asignado al entrenador correcto y reasígnalos manualmente si es necesario.
    """
    Equipo = apps.get_model('stats_app', 'Equipo')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    equipos_sin_dueno = Equipo.objects.filter(entrenador__isnull=True)
    if not equipos_sin_dueno.exists():
        return

    default_user = User.objects.filter(is_superuser=True).order_by('id').first()
    if default_user is None:
        default_user = User.objects.order_by('id').first()
    if default_user is None:
        # Base de datos sin usuarios todavía: no hay nada que backfillear.
        return

    equipos_sin_dueno.update(entrenador_id=default_user.id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('stats_app', '0008_equipo_entrenador'),
    ]

    operations = [
        migrations.RunPython(asignar_entrenador_por_defecto, noop_reverse),
    ]
