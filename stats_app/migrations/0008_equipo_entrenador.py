from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('stats_app', '0007_partido_puntos_por_set_partido_puntos_set_decisivo_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='equipo',
            name='entrenador',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='equipos',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Entrenador',
                help_text='Usuario propietario del equipo. Garantiza el aislamiento de datos entre entrenadores.',
            ),
        ),
    ]
