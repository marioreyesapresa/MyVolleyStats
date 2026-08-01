# Generated manually for Modo Trazo (destino saque/ataque)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stats_app', '0017_ajuste_marcador'),
    ]

    operations = [
        migrations.AddField(
            model_name='registroestadistica',
            name='zona_destino',
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text='Zona del campo rival (1-6, numeración FIVB) hacia la que fue el saque o ataque. Nulo si no se registró destino (preferencia apagada, Sin destino, u otras acciones).',
                null=True,
                verbose_name='Zona destino',
            ),
        ),
    ]
