"""Crea un equipo y plantilla ficticios para pruebas locales de scout.

Solo se ejecuta con DJANGO_DEBUG=True. En producción (Cloud Run) aborta
para evitar datos de prueba en la base de datos real.

Uso:

    python manage.py crear_equipo_prueba
    python manage.py crear_equipo_prueba --usuario marioreyes
    python manage.py crear_equipo_prueba --reset
"""
from datetime import date, time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stats_app.models import Equipo, Jugadora, Partido

EQUIPO_NOMBRE = '[DEV] Alevín Prueba'
TEMPORADA = '2025/2026'

JUGADORAS = [
    (12, 'Victoria', 'García', 'RECEPTORA'),
    (29, 'Ana', 'Martín', 'COLOCADORA'),
    (18, 'Lucía', 'Sánchez', 'CENTRAL'),
    (25, 'Belén', 'López', 'OPUESTA'),
    (7, 'Clara', 'Ruiz', 'RECEPTORA'),
    (11, 'Sofía', 'Díaz', 'CENTRAL'),
    (3, 'Elena', 'Torres', 'RECEPTORA'),
    (14, 'Paula', 'Moreno', 'OPUESTA'),
]


class Command(BaseCommand):
    help = 'Crea equipo y plantilla ficticios para pruebas (solo con DEBUG=True).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--usuario',
            help='Username del entrenador propietario (por defecto: primer superusuario).',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Borra el equipo [DEV] y lo vuelve a crear desde cero.',
        )
        parser.add_argument(
            '--sin-partido',
            action='store_true',
            help='No crea el partido de prueba contra Entreolivos.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'Este comando solo está permitido en local (DJANGO_DEBUG=True). '
                'No se ejecuta en producción.'
            )

        User = get_user_model()
        username = options.get('usuario')
        if username:
            try:
                entrenador = User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f'No existe el usuario "{username}".') from exc
        else:
            entrenador = User.objects.filter(is_superuser=True).order_by('id').first()
            if entrenador is None:
                entrenador = User.objects.order_by('id').first()
            if entrenador is None:
                raise CommandError('No hay usuarios en la base de datos. Crea uno antes.')

        with transaction.atomic():
            if options['reset']:
                deleted, _ = Equipo.objects.filter(nombre=EQUIPO_NOMBRE).delete()
                if deleted:
                    self.stdout.write(self.style.WARNING(f'Equipo {EQUIPO_NOMBRE!r} eliminado.'))

            equipo, created = Equipo.objects.get_or_create(
                nombre=EQUIPO_NOMBRE,
                temporada=TEMPORADA,
                defaults={
                    'entrenador': entrenador,
                    'categoria': 'ALEVIN',
                    'entrenador_principal': 'Entrenador de Prueba',
                },
            )
            if not created and equipo.entrenador_id != entrenador.id:
                equipo.entrenador = entrenador
                equipo.save(update_fields=['entrenador'])

            jugadoras_creadas = 0
            for dorsal, nombre, apellidos, posicion in JUGADORAS:
                _, j_created = Jugadora.objects.get_or_create(
                    equipo=equipo,
                    dorsal=dorsal,
                    defaults={
                        'nombre': nombre,
                        'apellidos': apellidos,
                        'posicion': posicion,
                        'fecha_nacimiento': date(2014, 6, 15),
                    },
                )
                if j_created:
                    jugadoras_creadas += 1

            partido = None
            if not options['sin_partido']:
                partido, p_created = Partido.objects.get_or_create(
                    equipo=equipo,
                    rival='[DEV] Entreolivos',
                    fecha=date.today(),
                    defaults={
                        'hora': time(18, 0),
                        'local': True,
                        'lugar': 'Pabellón de Prueba',
                        'modalidad': 'MINIVOLEY',
                        'puntos_por_set': 25,
                        'puntos_set_decisivo': 15,
                        'sets_para_ganar': 3,
                    },
                )
                if p_created:
                    self.stdout.write(f'  Partido de prueba creado: vs {partido.rival}')

        self.stdout.write(self.style.SUCCESS(
            f'Equipo listo: {equipo.nombre} ({equipo.jugadoras.count()} jugadoras, '
            f'entrenador: {entrenador.username})'
        ))
        if jugadoras_creadas:
            self.stdout.write(f'  {jugadoras_creadas} jugadoras nuevas añadidas.')
        if partido:
            self.stdout.write(f'  Abre el partido desde el dashboard o /partido/{partido.id}/scout/')
