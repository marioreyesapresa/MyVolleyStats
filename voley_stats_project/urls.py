from django.contrib import admin
from django.urls import path, include
from stats_app.views.auth import RegistroEntrenadorView
from stats_app.views.pwa import service_worker_view

urlpatterns = [
    path('admin/', admin.site.urls),
    # Servido explícitamente en la raíz (no en /static/) para que el scope
    # por defecto del Service Worker sea "/" y pueda controlar toda la app.
    # Ver stats_app/views/pwa.py para el porqué.
    path('service-worker.js', service_worker_view, name='service_worker'),
    path('accounts/register/', RegistroEntrenadorView.as_view(), name='register'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('stats_app.urls')),
]
