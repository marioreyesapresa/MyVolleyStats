"""Crea partidos de demostración para el dashboard (Sprint 1).

Solo se ejecuta con DJANGO_DEBUG=True.

Cubre:
  - Héroe: próximo cruce (hoy/mañana)
  - Lista de próximos: un partido posterior (no duplica el héroe)
  - Historial 3–1 con parciales 25-18 · 22-25 · 25-19 · 25-21
  - Historial 0–3 (derrota)
  - Historial sin scout

Uso:

    python manage.py crear_datos_dashboard
    python manage.py crear_datos_dashboard --usuario marioreyes
    python manage.py crear_datos_dashboard --reset
"""
from datetime import date, timedelta, time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stats_app.models import Equipo, Jugadora, Partido, RegistroEstadistica

EQUIPO_NOMBRE = '[DEV] Dashboard Sprint'
TEMPORADA = '2025/2026'

JUGADORAS = [
    (4, 'Laura', 'Gómez', 'RECEPTORA'),
    (7, 'Marta', 'Sanz', 'COLOCADORA'),
    (9, 'Irene', 'Vega', 'CENTRAL'),
    (11, 'Nuria', 'Gil', 'OPUESTA'),
    (12, 'Sara', 'Ortiz', 'RECEPTORA'),
    (15, 'Alba', 'Reyes', 'CENTRAL'),
    (2, 'Carmen', 'León', 'LIBERO'),
]


def _puntos_set(partido, jugadora, set_n, local, rival):
    registros = [
        RegistroEstadistica(
            partido=partido, jugadora=jugadora, tipo_fase='K1',
            accion='ATAQUE', calidad='++', set_numero=set_n,
        )
        for _ in range(local)
    ] + [
        RegistroEstadistica(
            partido=partido, jugadora=jugadora, tipo_fase='K1',
            accion='ATAQUE', calidad='--', set_numero=set_n,
        )
        for _ in range(rival)
    ]
    RegistroEstadistica.objects.bulk_create(registros)


class Command(BaseCommand):
    help = 'Crea partidos de demo para el dashboard (solo DEBUG=True).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--usuario',
            help='Username del entrenador (por defecto: marioreyes, si existe).',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Borra el equipo [DEV] Dashboard Sprint y lo recrea.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'Este comando solo está permitido en local (DJANGO_DEBUG=True).'
            )

        User = get_user_model()
        username = options.get('usuario')
        if username:
            try:
                entrenador = User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f'No existe el usuario "{username}".') from exc
        else:
            entrenador = User.objects.filter(username='marioreyes').first()
            if entrenador is None:
                entrenador = User.objects.filter(is_superuser=True).order_by('id').first()
            if entrenador is None:
                entrenador = User.objects.order_by('id').first()
            if entrenador is None:
                raise CommandError('No hay usuarios. Crea uno antes.')

        hoy = date.today()

        with transaction.atomic():
            if options['reset']:
                deleted, _ = Equipo.objects.filter(
                    nombre=EQUIPO_NOMBRE, entrenador=entrenador,
                ).delete()
                if deleted:
                    self.stdout.write(self.style.WARNING(f'Equipo {EQUIPO_NOMBRE!r} eliminado.'))

            equipo, _ = Equipo.objects.update_or_create(
                nombre=EQUIPO_NOMBRE,
                entrenador=entrenador,
                defaults={
                    'temporada': TEMPORADA,
                    'categoria': 'CADETE',
                    'entrenador_principal': 'Demo Dashboard',
                },
            )

            jugadora_ref = None
            for dorsal, nombre, apellidos, posicion in JUGADORAS:
                jugadora, _ = Jugadora.objects.get_or_create(
                    equipo=equipo,
                    dorsal=dorsal,
                    defaults={
                        'nombre': nombre,
                        'apellidos': apellidos,
                        'posicion': posicion,
                        'fecha_nacimiento': date(2010, 5, 1),
                    },
                )
                if dorsal == 4:
                    jugadora_ref = jugadora
            if jugadora_ref is None:
                jugadora_ref = equipo.jugadoras.first()

            Partido.objects.filter(equipo=equipo).delete()

            heroe = Partido.objects.create(
                equipo=equipo,
                fecha=hoy,
                hora=time(10, 30),
                rival='CV Alcorcón',
                local=True,
                lugar='Pabellón Municipal Alcorcón',
                modalidad='VOLEY',
            )
            posterior = Partido.objects.create(
                equipo=equipo,
                fecha=hoy + timedelta(days=7),
                hora=time(18, 0),
                rival='CV Pozuelo',
                local=False,
                lugar='Polideportivo El Torreón',
                modalidad='VOLEY',
            )
            victoria = Partido.objects.create(
                equipo=equipo,
                fecha=hoy - timedelta(days=7),
                hora=time(12, 0),
                rival='CV Majadahonda',
                local=True,
                lugar='Pabellón El Carrascal',
                modalidad='VOLEY',
                finalizado=True,
            )
            _puntos_set(victoria, jugadora_ref, 1, 25, 18)
            _puntos_set(victoria, jugadora_ref, 2, 22, 25)
            _puntos_set(victoria, jugadora_ref, 3, 25, 19)
            _puntos_set(victoria, jugadora_ref, 4, 25, 21)

            derrota = Partido.objects.create(
                equipo=equipo,
                fecha=hoy - timedelta(days=14),
                hora=time(17, 0),
                rival='CV Las Rozas',
                local=False,
                lugar='Pabellón Las Rozas',
                modalidad='VOLEY',
                finalizado=True,
            )
            _puntos_set(derrota, jugadora_ref, 1, 18, 25)
            _puntos_set(derrota, jugadora_ref, 2, 20, 25)
            _puntos_set(derrota, jugadora_ref, 3, 16, 25)

            sin_scout = Partido.objects.create(
                equipo=equipo,
                fecha=hoy - timedelta(days=21),
                hora=time(11, 0),
                rival='CV Boadilla',
                local=True,
                lugar='Pabellón Príncipe de Asturias',
                modalidad='VOLEY',
                finalizado=True,
            )

        self.stdout.write(self.style.SUCCESS(
            f'Dashboard listo para {entrenador.username} → {equipo.nombre}'
        ))
        self.stdout.write(f'  Héroe:        vs {heroe.rival}  {heroe.fecha} {heroe.hora.strftime("%H:%M")}  ({heroe.lugar})')
        self.stdout.write(f'  Otro próximo: vs {posterior.rival}  {posterior.fecha}')
        self.stdout.write(f'  Historial:    vs {victoria.rival}  3–1  (25-18 · 22-25 · 25-19 · 25-21)')
        self.stdout.write(f'  Historial:    vs {derrota.rival}  0–3')
        self.stdout.write(f'  Historial:    vs {sin_scout.rival}  sin scout')
        self.stdout.write('  Entra en Inicio y prueba las pestañas Próximos / Historial.')
