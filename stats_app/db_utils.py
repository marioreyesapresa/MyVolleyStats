"""Resiliencia de base de datos a nivel de vista (complementa CONN_HEALTH_CHECKS).

`CONN_HEALTH_CHECKS` (settings.py) evita reutilizar una conexión persistente
ya muerta, pero no cubre el caso en que la conexión se corta *mientras* la
petición actual está en curso (un micro-corte de red entre Cloud Run y Neon
a mitad de un INSERT/SELECT). En ese caso Django/psycopg lanzan
`OperationalError` o `InterfaceError` y, sin más, el usuario recibe un 500.

`reintentar_en_error_transitorio` reintenta la vista completa un par de
veces, cerrando primero la conexión rota (`close_old_connections`) para que
Django abra una nueva antes del siguiente intento. Es seguro para las vistas
de scouting porque:

    - Se aplica solo a errores de conexión (no a errores de datos/lógica).
    - Si el corte ocurre ANTES de que el servidor confirme el commit, no ha
      habido escritura real: reintentar es equivalente a la petición
      original y no duplica datos.
    - Si el corte ocurre DESPUÉS del commit pero antes de que la respuesta
      llegue al cliente, el JS del frontend (ver modo_partido.html) también
      reintentará; en el peor caso el entrenador ve un registro duplicado,
      detectable y corregible con el botón "eliminar" — un compromiso
      aceptable frente a "perder la estadística" o "romper el marcador".
"""
import functools
import logging
import time

from django.db import OperationalError, InterfaceError
from django.db import close_old_connections

logger = logging.getLogger('stats_app.db_resilience')

_ERRORES_TRANSITORIOS = (OperationalError, InterfaceError)


def reintentar_en_error_transitorio(max_intentos=3, backoff_base=0.2):
    """Decorador para vistas (function-based o método `.post`/`.get` de View).

    Reintenta hasta `max_intentos` veces si la BD lanza un error de conexión
    transitorio, con un backoff corto entre intentos.
    """

    def decorador(vista_func):
        @functools.wraps(vista_func)
        def envoltura(*args, **kwargs):
            ultimo_error = None
            for intento in range(1, max_intentos + 1):
                try:
                    return vista_func(*args, **kwargs)
                except _ERRORES_TRANSITORIOS as exc:
                    ultimo_error = exc
                    logger.warning(
                        "Error transitorio de BD en %s (intento %d/%d): %s",
                        getattr(vista_func, '__qualname__', vista_func.__name__),
                        intento, max_intentos, exc,
                    )
                    # Descarta la conexión rota; Django abrirá una nueva en
                    # el siguiente acceso a la BD.
                    close_old_connections()
                    if intento < max_intentos:
                        time.sleep(backoff_base * intento)
            raise ultimo_error
        return envoltura

    return decorador
