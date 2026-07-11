import json

from django.http import JsonResponse, Http404
from django.utils import timezone
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin

from ..forms import NotaPartidoForm
from ..models import NotaPartido, Partido, Jugadora
from ..security import log_intento_acceso_no_autorizado
from ..services.informes_cache import invalidar_cache_informes_partido


def _partido_del_entrenador(request, partido_id):
    try:
        return Partido.objects.get(pk=partido_id, equipo__entrenador=request.user)
    except Partido.DoesNotExist:
        log_intento_acceso_no_autorizado(request, 'Partido', partido_id)
        raise Http404


def _nota_del_partido(request, partido, nota_id):
    try:
        return NotaPartido.objects.get(pk=nota_id, partido=partido)
    except NotaPartido.DoesNotExist:
        log_intento_acceso_no_autorizado(request, 'NotaPartido', nota_id)
        raise Http404


def _jugadora_del_equipo(request, jugadora_id, equipo):
    try:
        return Jugadora.objects.get(pk=jugadora_id, equipo=equipo)
    except Jugadora.DoesNotExist:
        log_intento_acceso_no_autorizado(request, 'Jugadora', jugadora_id)
        raise Http404


def _parsear_json(request):
    try:
        data = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None, JsonResponse({'status': 'error', 'mensaje': 'JSON inválido'}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({'status': 'error', 'mensaje': 'Se esperaba un objeto JSON'}, status=400)
    return data, None


def _nota_a_dict(nota):
    return {
        'id': nota.id,
        'texto': nota.texto,
        'set_numero': nota.set_numero,
        'jugadora_id': nota.jugadora_id,
        'jugadora_dorsal': nota.jugadora.dorsal if nota.jugadora_id else None,
        'jugadora_nombre': nota.jugadora.nombre if nota.jugadora_id else None,
        'creado_en': timezone.localtime(nota.creado_en).strftime('%H:%M'),
        'creado_en_fecha': timezone.localtime(nota.creado_en).strftime('%d/%m/%Y %H:%M'),
    }


class ListNotasPartidoAPI(LoginRequiredMixin, View):
    def get(self, request, partido_id):
        partido = _partido_del_entrenador(request, partido_id)
        notas = NotaPartido.objects.filter(partido=partido).select_related('jugadora')
        return JsonResponse({
            'status': 'ok',
            'notas': [_nota_a_dict(n) for n in notas],
        })


class CrearNotaPartidoAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        partido = _partido_del_entrenador(request, partido_id)
        data, err = _parsear_json(request)
        if err:
            return err

        form = NotaPartidoForm(data)
        if not form.is_valid():
            return JsonResponse({'status': 'error', 'mensaje': form.errors.as_text()}, status=400)

        cd = form.cleaned_data
        jugadora = None
        if cd.get('jugadora_id'):
            jugadora = _jugadora_del_equipo(request, cd['jugadora_id'], partido.equipo)

        nota = NotaPartido.objects.create(
            partido=partido,
            jugadora=jugadora,
            set_numero=cd.get('set_numero') or 1,
            texto=cd['texto'].strip(),
        )
        invalidar_cache_informes_partido(partido.pk)
        return JsonResponse({'status': 'ok', 'nota': _nota_a_dict(nota)})


class ActualizarNotaPartidoAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id, nota_id):
        partido = _partido_del_entrenador(request, partido_id)
        nota = _nota_del_partido(request, partido, nota_id)
        data, err = _parsear_json(request)
        if err:
            return err

        form = NotaPartidoForm(data)
        if not form.is_valid():
            return JsonResponse({'status': 'error', 'mensaje': form.errors.as_text()}, status=400)

        cd = form.cleaned_data
        jugadora = None
        if cd.get('jugadora_id'):
            jugadora = _jugadora_del_equipo(request, cd['jugadora_id'], partido.equipo)

        nota.jugadora = jugadora
        nota.set_numero = cd.get('set_numero') or nota.set_numero
        nota.texto = cd['texto'].strip()
        nota.save(update_fields=['jugadora', 'set_numero', 'texto', 'actualizado_en'])
        invalidar_cache_informes_partido(partido.pk)
        return JsonResponse({'status': 'ok', 'nota': _nota_a_dict(nota)})


class EliminarNotaPartidoAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id, nota_id):
        partido = _partido_del_entrenador(request, partido_id)
        nota = _nota_del_partido(request, partido, nota_id)
        nota.delete()
        invalidar_cache_informes_partido(partido.pk)
        return JsonResponse({'status': 'ok'})
