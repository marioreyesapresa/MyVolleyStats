from django.shortcuts import render, redirect
from django.views.generic import View, CreateView, UpdateView, DeleteView, ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from ..models import Equipo, Jugadora, Partido

class ConfiguracionView(LoginRequiredMixin, View):
    template_name = 'stats_app/configuracion.html'

    def get(self, request):
        return render(request, self.template_name)


class DashboardView(LoginRequiredMixin, ListView):
    model = Partido
    template_name = 'stats_app/dashboard.html'
    context_object_name = 'partidos'

    def get_queryset(self):
        return Partido.objects.all().order_by('-fecha', '-hora')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['equipos'] = Equipo.objects.all().order_by('nombre')
        return context

# CRUD EQUIPO
class EquipoCreateView(LoginRequiredMixin, CreateView):
    model = Equipo
    fields = ['nombre', 'temporada', 'categoria']
    template_name = 'stats_app/admin/equipo_form.html'

    def get_success_url(self):
        return reverse_lazy('stats_app:equipos_list')

class EquipoUpdateView(LoginRequiredMixin, UpdateView):
    model = Equipo
    fields = ['nombre', 'temporada', 'categoria']
    template_name = 'stats_app/admin/equipo_form.html'

    def get_success_url(self):
        return reverse_lazy('stats_app:equipos_list')

class EquipoDeleteView(LoginRequiredMixin, DeleteView):
    model = Equipo
    template_name = 'stats_app/admin/equipo_confirm_delete.html'
    success_url = reverse_lazy('stats_app:dashboard')

# CRUD JUGADORA
class JugadoraCreateView(LoginRequiredMixin, CreateView):
    model = Jugadora
    fields = ['equipo', 'nombre', 'apellidos', 'dorsal', 'posicion', 'fecha_nacimiento']
    template_name = 'stats_app/admin/jugadora_form.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['fecha_nacimiento'].required = True
        return form

    def get_initial(self):
        initial = super().get_initial()
        equipo_id = self.request.GET.get('equipo_id')
        if equipo_id:
            initial['equipo'] = equipo_id
        return initial

class JugadoraUpdateView(LoginRequiredMixin, UpdateView):
    model = Jugadora
    fields = ['equipo', 'nombre', 'apellidos', 'dorsal', 'posicion', 'fecha_nacimiento']
    template_name = 'stats_app/admin/jugadora_form.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['fecha_nacimiento'].required = True
        return form

class JugadoraDeleteView(LoginRequiredMixin, DeleteView):
    model = Jugadora
    template_name = 'stats_app/admin/jugadora_confirm_delete.html'
    success_url = reverse_lazy('stats_app:dashboard')

# CRUD PARTIDO
class PartidoCreateView(LoginRequiredMixin, CreateView):
    model = Partido
    fields = ['equipo', 'fecha', 'hora', 'rival', 'local', 'lugar', 'modalidad']
    template_name = 'stats_app/admin/partido_form.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def get_initial(self):
        initial = super().get_initial()
        equipo_id = self.request.GET.get('equipo_id')
        if equipo_id:
            initial['equipo'] = equipo_id
        return initial

class PartidoUpdateView(LoginRequiredMixin, UpdateView):
    model = Partido
    fields = ['equipo', 'fecha', 'hora', 'rival', 'local', 'lugar', 'modalidad']
    template_name = 'stats_app/admin/partido_form.html'
    success_url = reverse_lazy('stats_app:dashboard')

class PartidoDeleteView(LoginRequiredMixin, DeleteView):
    model = Partido
    template_name = 'stats_app/admin/partido_confirm_delete.html'
    success_url = reverse_lazy('stats_app:dashboard')

class EquipoListView(LoginRequiredMixin, ListView):
    model = Equipo
    template_name = 'stats_app/equipos_list.html'
    context_object_name = 'equipos'

    def get_queryset(self):
        return Equipo.objects.all().order_by('nombre')
