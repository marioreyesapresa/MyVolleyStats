from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect
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
        login(
            self.request,
            self.object,
            backend='stats_app.backends.EmailOrUsernameBackend',
        )
        return response


def csrf_failure(request, reason=''):
    """Sustituye la pantalla amarilla de CSRF por un redirect amigable al login."""
    messages.error(
        request,
        'Tu sesión ha cambiado o caducado. Por favor, inicia sesión de nuevo.',
    )
    return redirect('login')
