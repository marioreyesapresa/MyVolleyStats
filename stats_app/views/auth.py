from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from ..forms import RegistroEntrenadorForm


class RegistroEntrenadorView(CreateView):
    """Registro de entrenadores. Se puede desactivar con ALLOW_PUBLIC_REGISTRATION=False."""

    form_class = RegistroEntrenadorForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('stats_app:dashboard')

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, 'ALLOW_PUBLIC_REGISTRATION', True):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(
            self.request,
            self.object,
            backend='stats_app.backends.EmailOrUsernameBackend',
        )
        return response


class PoliticaPrivacidadView(TemplateView):
    template_name = 'legal/privacidad.html'


class TerminosServicioView(TemplateView):
    template_name = 'legal/terminos.html'


def csrf_failure(request, reason=''):
    """Sustituye la pantalla amarilla de CSRF por un redirect amigable al login."""
    messages.error(
        request,
        'Tu sesión ha cambiado o caducado. Por favor, inicia sesión de nuevo.',
    )
    return redirect('login')
