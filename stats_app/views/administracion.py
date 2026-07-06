from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import View, CreateView, UpdateView, DeleteView, ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from ..models import Equipo, Jugadora, Partido
from ..forms import JugadoraForm
from ..security import AuditoriaAccesoMixin


class ConfiguracionView(LoginRequiredMixin, View):
    template_name = 'stats_app/configuracion.html'

    def get(self, request):
        return render(request, self.template_name)


class DashboardView(LoginRequiredMixin, ListView):
    model = Partido
    template_name = 'stats_app/dashboard.html'
    context_object_name = 'partidos'

    def get_queryset(self):
        return Partido.objects.filter(equipo__entrenador=self.request.user).order_by('-fecha', '-hora')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['equipos'] = Equipo.objects.filter(entrenador=self.request.user).order_by('nombre')
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


# ─────────────────────────────────────────────────────────────────────────────
# CRUD JUGADORA — el desplegable de equipo y las consultas se restringen
# siempre a equipos propiedad del usuario autenticado
# ─────────────────────────────────────────────────────────────────────────────
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
