import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.generic import View

from ..security import log_client_error, ocultar_detalle_interno

logger = logging.getLogger('stats_app.client_errors')


class ClientErrorAPI(LoginRequiredMixin, View):
    """Recibe errores JS del Scout y los deja en Cloud Run Logging / Sentry."""

    def post(self, request):
        try:
            try:
                data = json.loads(request.body or b'{}')
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                return JsonResponse({'status': 'error', 'mensaje': 'JSON inválido'}, status=400)
            if not isinstance(data, dict):
                return JsonResponse({'status': 'error', 'mensaje': 'Se esperaba un objeto JSON'}, status=400)

            log_client_error(request, data)

            # Si Sentry está activo, también lo reportamos como mensaje.
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag('origen', 'cliente_scout')
                    if data.get('partido_id') is not None:
                        scope.set_tag('partido_id', str(data.get('partido_id')))
                    sentry_sdk.capture_message(
                        str(data.get('mensaje') or data.get('message') or 'Error cliente')[:200],
                        level='warning',
                    )
            except Exception:
                pass

            return JsonResponse({'status': 'ok'})
        except Exception as e:
            logger.exception('Error al registrar telemetría de cliente')
            return JsonResponse({'status': 'error', 'mensaje': ocultar_detalle_interno(e)}, status=400)
