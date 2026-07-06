from django.contrib import admin
from django.urls import path, include
from stats_app.views.auth import RegistroEntrenadorView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/register/', RegistroEntrenadorView.as_view(), name='register'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('stats_app.urls')),
]
