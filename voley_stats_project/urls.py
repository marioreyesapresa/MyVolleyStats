from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

from stats_app.views.auth import (
    PoliticaPrivacidadView,
    RegistroEntrenadorView,
    TerminosServicioView,
)
from stats_app.views.pwa import service_worker_view
from stats_app.forms import LoginForm

_admin_path = f"{getattr(settings, 'DJANGO_ADMIN_URL', 'admin').strip('/')}/"

urlpatterns = [
    path(_admin_path, admin.site.urls),
    # Servido explícitamente en la raíz (no en /static/) para que el scope
    # por defecto del Service Worker sea "/" y pueda controlar toda la app.
    # Ver stats_app/views/pwa.py para el porqué.
    path('service-worker.js', service_worker_view, name='service_worker'),
    path('legal/privacidad/', PoliticaPrivacidadView.as_view(), name='privacidad'),
    path('legal/terminos/', TerminosServicioView.as_view(), name='terminos'),
    path('accounts/register/', RegistroEntrenadorView.as_view(), name='register'),
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(
            authentication_form=LoginForm,
            template_name='registration/login.html',
        ),
        name='login',
    ),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path(
        'accounts/password_reset/',
        auth_views.PasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.html',
            subject_template_name='registration/password_reset_subject.txt',
        ),
        name='password_reset',
    ),
    path(
        'accounts/password_reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'accounts/reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
        ),
        name='password_reset_confirm',
    ),
    path(
        'accounts/reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
    path('', include('stats_app.urls')),
]

handler500 = 'stats_app.views.errors.handler500'
