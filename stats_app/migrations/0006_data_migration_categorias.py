from django.db import migrations

def migrate_categories_forward(apps, schema_editor):
    Equipo = apps.get_model('stats_app', 'Equipo')
    
    for eq in Equipo.objects.all():
        old_cat = eq.categoria.strip().upper() if eq.categoria else ''
        if 'BENJ' in old_cat:
            new_cat = 'BENJAMIN'
        elif 'ALEV' in old_cat:
            new_cat = 'ALEVIN'
        elif 'INFA' in old_cat:
            new_cat = 'INFANTIL'
        elif 'CADE' in old_cat:
            new_cat = 'CADETE'
        elif 'JUVE' in old_cat:
            new_cat = 'JUVENIL'
        elif 'JUNI' in old_cat:
            new_cat = 'JUNIOR'
        elif 'SENI' in old_cat:
            new_cat = 'SENIOR'
        else:
            new_cat = 'SENIOR'
            
        eq.categoria = new_cat
        eq.save()

def migrate_categories_backward(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('stats_app', '0005_rotacionset_libero1_rotacionset_libero2_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_categories_forward, migrate_categories_backward),
    ]
