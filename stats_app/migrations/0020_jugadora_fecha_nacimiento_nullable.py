# SQLite local (dbs antiguas) puede tener fecha_nacimiento NOT NULL
# aunque 0001_initial ya define el campo como opcional.

from django.db import migrations, models


def relajar_fecha_nacimiento(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE stats_app_jugadora ALTER COLUMN fecha_nacimiento DROP NOT NULL'
            )
        return
    if connection.vendor != 'sqlite':
        return

    with connection.cursor() as cursor:
        cursor.execute('PRAGMA table_info(stats_app_jugadora)')
        info = cursor.fetchall()
    fecha = next((col for col in info if col[1] == 'fecha_nacimiento'), None)
    if not fecha or fecha[3] == 0:
        return

    Jugadora = apps.get_model('stats_app', 'Jugadora')
    old_field = models.DateField()
    old_field.set_attributes_from_name('fecha_nacimiento')
    old_field.model = Jugadora
    new_field = models.DateField(blank=True, null=True)
    new_field.set_attributes_from_name('fecha_nacimiento')
    new_field.model = Jugadora
    schema_editor.alter_field(Jugadora, old_field, new_field)


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('stats_app', '0019_lineuppreset'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(relajar_fecha_nacimiento, noop),
            ],
            state_operations=[],
        ),
    ]
