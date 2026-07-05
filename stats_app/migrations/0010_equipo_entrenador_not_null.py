from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('stats_app', '0009_backfill_entrenador'),
    ]

    operations = [
        migrations.AlterField(
            model_name='equipo',
            name='entrenador',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='equipos',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Entrenador',
                help_text='Usuario propietario del equipo. Garantiza el aislamiento de datos entre entrenadores.',
            ),
        ),
    ]
