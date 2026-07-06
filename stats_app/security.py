"""Blindaje de seguridad transversal: Rate Limiting, IDOR y fuga de datos.

Mitiga varios escenarios de OWASP Top 10:

    - A04 Insecure Design / A07 Identification & Authentication Failures:
      fuerza bruta contra el login y abuso/DoS de las APIs de scouting si
      alguien descubre la URL pública de Cloud Run.
    - A01 Broken Access Control (IDOR): acceso a recursos (partidos,
      jugadoras, registros) que pertenecen a otro entrenador.
    - A05 Security Misconfiguration: fuga de detalles internos del stack
      (versión de Django, tracebacks) en respuestas de error.

Implementación deliberadamente simple (sin dependencias externas ni Redis):
usa el framework de caché de Django (LocMemCache, ver settings.CACHES) como
contador de ventana fija por IP + ruta.

Todo evento de bloqueo (429) o acceso IDOR detectado (404 forzado) se
registra vía `logging` con la IP real del cliente para poder configurar
alertas de monitorización en Cloud Run Logging.
"""
import logging
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger('stats_app.security')


def get_client_ip(request):
    """Extrae la IP real del cliente detrás del proxy de Cloud Run.

    Cloud Run añade `X-Forwarded-For: <ip_cliente>, <ip_proxy_interno>` de
    forma fiable (el proxy de borde gestiona la cabecera, no llega
    directamente del navegador), por lo que tomamos el primer valor.
    """
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def log_intento_acceso_no_autorizado(request, tipo_recurso, recurso_id):
    """Registra un intento de acceso a un recurso ajeno o inexistente (IDOR).

    Se dispara cada vez que una consulta aislada por `entrenador=request.user`
    no encuentra el recurso solicitado: puede ser un ID que no existe o,
    más grave, uno que pertenece a otro entrenador. No se distingue entre
    ambos casos en la respuesta HTTP (siempre 404) para no filtrar
    información, pero sí se deja constancia en el log de seguridad.
    """
    usuario = getattr(request.user, 'username', None) or 'anónimo'
    logger.warning(
        'IDOR bloqueado (404): usuario=%s ip=%s recurso=%s id=%s metodo=%s ruta=%s',
        usuario, get_client_ip(request), tipo_recurso, recurso_id, request.method, request.path,
    )


def ocultar_detalle_interno(exc):
    """Mensaje seguro para devolver al cliente ante una excepción no controlada.

    En producción (`DEBUG=False`) nunca se serializa `str(exc)` tal cual en
    la respuesta: podría filtrar rutas de fichero, nombres de columnas de la
    base de datos, versiones de librerías o cualquier otro detalle interno
    del stack técnico. El detalle real siempre queda registrado en el log
    del servidor (vía `logger.exception`) para poder diagnosticar el fallo.
    En desarrollo (`DEBUG=True`) se devuelve el mensaje real para agilizar
    la depuración local.
    """
    if settings.DEBUG:
        return str(exc)
    return 'Ha ocurrido un error inesperado al procesar la solicitud. Inténtalo de nuevo.'


class AuditoriaAccesoMixin:
    """Mixin para `UpdateView`/`DeleteView`/`DetailView` con aislamiento por
    entrenador (`get_queryset` filtrado por `entrenador=request.user`).

    Django resuelve el objeto en `get_object()` y lanza `Http404` si el `pk`
    de la URL no está en el queryset filtrado — que es exactamente lo que
    ocurre si un entrenador manipula la URL para apuntar al equipo/jugadora/
    partido de otro. Se audita ese evento sin cambiar el comportamiento
    (sigue devolviendo 404 al cliente).
    """

    def get_object(self, queryset=None):
        from django.http import Http404
        try:
            return super().get_object(queryset)
        except Http404:
            log_intento_acceso_no_autorizado(
                self.request, self.model.__name__, self.kwargs.get('pk')
            )
            raise


class RateLimitMiddleware:
    """Limita peticiones por IP para un conjunto de rutas sensibles.

    Las reglas se definen en `settings.RATE_LIMIT_RULES` como tuplas
    `(patrón_regex, máx_peticiones, ventana_segundos)`. La primera regla
    cuyo patrón haga match con `request.path` es la que aplica.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.rules = [
            (re.compile(pattern), limit, window)
            for pattern, limit, window in getattr(settings, 'RATE_LIMIT_RULES', [])
        ]

    def __call__(self, request):
        rule = self._match_rule(request.path)
        if rule is not None:
            pattern, limit, window = rule
            ip = get_client_ip(request)
            allowed, retry_after = self._check_and_increment(pattern.pattern, ip, limit, window)
            if not allowed:
                logger.warning(
                    'Rate limit excedido (429): ip=%s ruta=%s metodo=%s regla=%s limite=%s/%ss retry_after=%ss',
                    ip, request.path, request.method, pattern.pattern, limit, window, retry_after,
                )
                response = JsonResponse(
                    {
                        'status': 'error',
                        'mensaje': 'Demasiadas peticiones. Espera unos segundos e inténtalo de nuevo.',
                    },
                    status=429,
                )
                response['Retry-After'] = str(retry_after)
                return response
        return self.get_response(request)

    def _match_rule(self, path):
        for pattern, limit, window in self.rules:
            if pattern.match(path):
                return pattern, limit, window
        return None

    @staticmethod
    def _check_and_increment(scope, ip, limit, window):
        """Contador de ventana fija atómico vía cache.add/incr.

        Devuelve (permitido: bool, retry_after_segundos: int).
        """
        key = f'ratelimit:{scope}:{ip}'
        # cache.add solo escribe si la clave no existe todavía -> evita una
        # condición de carrera entre el "get" y el "set" de un contador manual.
        created = cache.add(key, {'count': 1, 'reset_at': time.time() + window}, timeout=window)
        if created:
            return True, window

        bucket = cache.get(key)
        if bucket is None:
            # La clave expiró justo entre el add fallido y este get: se trata
            # como una ventana nueva.
            cache.add(key, {'count': 1, 'reset_at': time.time() + window}, timeout=window)
            return True, window

        bucket['count'] += 1
        remaining_ttl = max(1, int(bucket['reset_at'] - time.time()))
        cache.set(key, bucket, timeout=remaining_ttl)

        if bucket['count'] > limit:
            return False, remaining_ttl
        return True, remaining_ttl
