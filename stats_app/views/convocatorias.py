import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import render, redirect
from django.views.generic import View

from ..models import Convocatoria, Partido
from ..security import log_intento_acceso_no_autorizado
from ..services.convocatoria import (
    MOTIVOS_BAJA,
    ConvocatoriaInvalida,
    guardar_convocatoria,
    lineas_para_pantalla,
    texto_whatsapp,
)


def _partido_del_entrenador(request, pk):
    try:
        return Partido.objects.select_related('equipo').get(
            pk=pk, equipo__entrenador=request.user,
        )
    except Partido.DoesNotExist:
        log_intento_acceso_no_autorizado(request, 'Partido', pk)
        raise Http404


def _tiene_lista(partido):
    try:
        return partido.convocatoria is not None
    except Convocatoria.DoesNotExist:
        return False


class ConvocatoriaPartidoView(LoginRequiredMixin, View):
    template_name = 'stats_app/convocatoria.html'

    def get(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        return self._render(request, partido)

    def post(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        raw = request.POST.get('lineas', '[]')
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            messages.error(request, 'No se ha podido leer la lista. Inténtalo de nuevo.')
            return self._render(request, partido)
        if not isinstance(payload, list):
            messages.error(request, 'No se ha podido leer la lista. Inténtalo de nuevo.')
            return self._render(request, partido)
        try:
            guardar_convocatoria(partido, payload)
        except ConvocatoriaInvalida as exc:
            messages.error(request, str(exc))
            return self._render(request, partido)
        messages.success(request, 'Convocatoria guardada.')
        return redirect('stats_app:partido_convocatoria', pk=partido.pk)

    def _render(self, request, partido):
        lineas = lineas_para_pantalla(partido)
        return render(request, self.template_name, {
            'partido': partido,
            'lineas': lineas,
            'motivos': MOTIVOS_BAJA.choices,
            'texto_whatsapp': texto_whatsapp(partido, lineas),
            'tiene_lista': _tiene_lista(partido),
        })
