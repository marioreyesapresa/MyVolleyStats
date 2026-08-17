from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import View, CreateView, UpdateView, DeleteView, ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from ..models import Equipo, Jugadora, Partido
from ..forms import JugadoraForm
from ..security import AuditoriaAccesoMixin, log_intento_acceso_no_autorizado
from ..services.reporting import marcador_resumen
from ..services.temporada import stats_jugadora_temporada
from ..services.plantilla_csv import (
    MAX_BYTES,
    aplicar_plantilla,
    exportar_plantilla_csv,
    nombre_archivo_csv,
    parsear_plantilla_csv,
)


class ConfiguracionView(LoginRequiredMixin, View):
    template_name = 'stats_app/configuracion.html'

    def get(self, request):
        return render(request, self.template_name)


class DashboardView(LoginRequiredMixin, ListView):
    model = Partido
    template_name = 'stats_app/dashboard.html'
    context_object_name = 'partidos'

    def get_queryset(self):
        return (
            Partido.objects.filter(equipo__entrenador=self.request.user)
            .select_related('equipo')
            .order_by('-fecha', '-hora')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['equipos'] = Equipo.objects.filter(entrenador=self.request.user).order_by('nombre')

        partidos = list(context['partidos'])
        hoy = timezone.localdate()

        upcoming = sorted(
            (p for p in partidos if not p.finalizado and p.fecha >= hoy),
            key=lambda p: (p.fecha, p.hora),
        )
        proximo = upcoming[0] if upcoming else None
        context['proximo_partido'] = proximo
        context['partidos_proximos'] = upcoming[1:] if proximo else []
        context['partidos_por_scoutar'] = sorted(
            (p for p in partidos if not p.finalizado and p.fecha < hoy),
            key=lambda p: (p.fecha, p.hora),
        )

        historial = [p for p in partidos if p.finalizado]
        for partido in historial:
            partido.marcador = marcador_resumen(partido)
        context['partidos_historial'] = historial
        context['tab_partidos_inicial'] = (
            'por-scoutar'
            if not upcoming and context['partidos_por_scoutar']
            else 'proximos'
        )
        return context


# ─────────────────────────────────────────────────────────────────────────────
# CRUD EQUIPO — cada entrenador solo ve/edita/elimina sus propios equipos
# ─────────────────────────────────────────────────────────────────────────────
class EquipoCreateView(LoginRequiredMixin, CreateView):
    model = Equipo
    fields = ['nombre', 'temporada', 'categoria']
    template_name = 'stats_app/admin/equipo_form.html'

    def form_valid(self, form):
        form.instance.entrenador = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('stats_app:equipos_list')


class EquipoUpdateView(LoginRequiredMixin, AuditoriaAccesoMixin, UpdateView):
    model = Equipo
    fields = ['nombre', 'temporada', 'categoria']
    template_name = 'stats_app/admin/equipo_form.html'

    def get_queryset(self):
        return Equipo.objects.filter(entrenador=self.request.user)

    def get_success_url(self):
        return reverse_lazy('stats_app:equipos_list')


class EquipoDeleteView(LoginRequiredMixin, AuditoriaAccesoMixin, DeleteView):
    model = Equipo
    template_name = 'stats_app/admin/equipo_confirm_delete.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_queryset(self):
        return Equipo.objects.filter(entrenador=self.request.user)


class EquipoListView(LoginRequiredMixin, ListView):
    model = Equipo
    template_name = 'stats_app/equipos_list.html'
    context_object_name = 'equipos'

    def get_queryset(self):
        return Equipo.objects.filter(entrenador=self.request.user).order_by('nombre')


def _equipo_del_entrenador(request, pk):
    try:
        return Equipo.objects.get(pk=pk, entrenador=request.user)
    except Equipo.DoesNotExist:
        log_intento_acceso_no_autorizado(request, 'Equipo', pk)
        raise Http404


class ExportarPlantillaCSVView(LoginRequiredMixin, View):
    def get(self, request, pk):
        equipo = _equipo_del_entrenador(request, pk)
        contenido = exportar_plantilla_csv(equipo)
        response = HttpResponse(contenido, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo_csv(equipo)}"'
        return response


class ImportarPlantillaCSVView(LoginRequiredMixin, View):
    def post(self, request, pk):
        equipo = _equipo_del_entrenador(request, pk)
        archivo = request.FILES.get('archivo')
        if not archivo:
            messages.error(request, 'Selecciona un archivo CSV.')
            return redirect('stats_app:equipos_list')
        if archivo.size and archivo.size > MAX_BYTES:
            messages.error(request, 'El archivo es demasiado grande (máx. 64 KB).')
            return redirect('stats_app:equipos_list')

        parseo = parsear_plantilla_csv(archivo.read())
        resultado = aplicar_plantilla(equipo, parseo.filas)
        partes = []
        if resultado.creadas or resultado.actualizadas:
            partes.append(
                f'Plantilla importada: {resultado.creadas} creadas, '
                f'{resultado.actualizadas} actualizadas.'
            )
            if parseo.sin_dorsal:
                partes.append(
                    f'{parseo.sin_dorsal} sin dorsal: así no se ven bien en la pizarra; '
                    'puedes editarlo luego en la ficha.'
                )
        errores = parseo.errores
        if errores:
            muestra = '; '.join(errores[:5])
            extra = f' (+{len(errores) - 5} más)' if len(errores) > 5 else ''
            partes.append(f'{len(errores)} filas con error: {muestra}{extra}')
        if not partes:
            messages.error(request, 'No hay filas válidas para importar.')
        elif resultado.creadas or resultado.actualizadas:
            texto = ' '.join(partes)
            if errores or parseo.sin_dorsal:
                messages.warning(request, texto)
            else:
                messages.success(request, texto)
        else:
            messages.error(request, partes[0] if partes else 'No hay filas válidas para importar.')
        return redirect('stats_app:equipos_list')


# ─────────────────────────────────────────────────────────────────────────────
# CRUD JUGADORA — el desplegable de equipo y las consultas se restringen
# siempre a equipos propiedad del usuario autenticado
# ─────────────────────────────────────────────────────────────────────────────
class JugadoraDetailView(LoginRequiredMixin, AuditoriaAccesoMixin, DetailView):
    model = Jugadora
    template_name = 'stats_app/jugadora_ficha.html'
    context_object_name = 'jugadora'

    def get_queryset(self):
        return (
            Jugadora.objects
            .filter(equipo__entrenador=self.request.user)
            .select_related('equipo')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['temporada'] = stats_jugadora_temporada(self.object)
        return context


class JugadoraCreateView(LoginRequiredMixin, CreateView):
    model = Jugadora
    form_class = JugadoraForm
    template_name = 'stats_app/admin/jugadora_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['entrenador'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        equipo_id = self.request.GET.get('equipo_id')
        if equipo_id:
            initial['equipo'] = equipo_id
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f'Jugadora {self.object.nombre} {self.object.apellidos} añadida correctamente. '
            'Puedes seguir añadiendo más jugadoras al mismo equipo.',
        )
        return response

    def get_success_url(self):
        return f"{reverse('stats_app:jugadora_nueva')}?equipo_id={self.object.equipo_id}"


class JugadoraUpdateView(LoginRequiredMixin, AuditoriaAccesoMixin, UpdateView):
    model = Jugadora
    form_class = JugadoraForm
    template_name = 'stats_app/admin/jugadora_form.html'

    def get_queryset(self):
        return Jugadora.objects.filter(equipo__entrenador=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['entrenador'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Jugadora actualizada correctamente.')
        return response

    def get_success_url(self):
        return reverse_lazy('stats_app:equipos_list')


class JugadoraDeleteView(LoginRequiredMixin, AuditoriaAccesoMixin, DeleteView):
    model = Jugadora
    template_name = 'stats_app/admin/jugadora_confirm_delete.html'

    def get_queryset(self):
        return Jugadora.objects.filter(equipo__entrenador=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, 'Jugadora eliminada correctamente.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('stats_app:equipos_list')


# ─────────────────────────────────────────────────────────────────────────────
# CRUD PARTIDO — idéntica lógica de aislamiento
# ─────────────────────────────────────────────────────────────────────────────
class PartidoCreateView(LoginRequiredMixin, CreateView):
    model = Partido
    fields = ['equipo', 'fecha', 'hora', 'rival', 'local', 'lugar', 'modalidad']
    template_name = 'stats_app/admin/partido_form.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['equipo'].queryset = Equipo.objects.filter(entrenador=self.request.user)
        return form

    def get_initial(self):
        initial = super().get_initial()
        equipo_id = self.request.GET.get('equipo_id')
        if equipo_id:
            initial['equipo'] = equipo_id
        return initial


class PartidoUpdateView(LoginRequiredMixin, AuditoriaAccesoMixin, UpdateView):
    model = Partido
    fields = ['equipo', 'fecha', 'hora', 'rival', 'local', 'lugar', 'modalidad']
    template_name = 'stats_app/admin/partido_form.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_queryset(self):
        return Partido.objects.filter(equipo__entrenador=self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['equipo'].queryset = Equipo.objects.filter(entrenador=self.request.user)
        return form


class PartidoDeleteView(LoginRequiredMixin, AuditoriaAccesoMixin, DeleteView):
    model = Partido
    template_name = 'stats_app/admin/partido_confirm_delete.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_queryset(self):
        return Partido.objects.filter(equipo__entrenador=self.request.user)
