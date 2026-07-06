from django.contrib.auth import login
from django.urls import reverse_lazy
from django.views.generic import CreateView

from ..forms import RegistroEntrenadorForm


class RegistroEntrenadorView(CreateView):
    """Registro público de entrenadores. Tras crear la cuenta, inicia sesión automáticamente."""

    form_class = RegistroEntrenadorForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response
