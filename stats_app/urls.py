from django.urls import path
from . import views

app_name = 'stats_app'

urlpatterns = [
    # Dashboard y CRUDs de administración
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('configuracion/', views.ConfiguracionView.as_view(), name='configuracion'),
    path('equipo/nuevo/', views.EquipoCreateView.as_view(), name='equipo_nuevo'),
    path('equipo/<int:pk>/editar/', views.EquipoUpdateView.as_view(), name='equipo_editar'),
    path('equipo/<int:pk>/eliminar/', views.EquipoDeleteView.as_view(), name='equipo_eliminar'),
    path('equipos/', views.EquipoListView.as_view(), name='equipos_list'),
    
    path('jugadora/nueva/', views.JugadoraCreateView.as_view(), name='jugadora_nueva'),
    path('jugadora/<int:pk>/editar/', views.JugadoraUpdateView.as_view(), name='jugadora_editar'),
    path('jugadora/<int:pk>/eliminar/', views.JugadoraDeleteView.as_view(), name='jugadora_eliminar'),
    
    path('partido/nuevo/', views.PartidoCreateView.as_view(), name='partido_nuevo'),
    path('partido/<int:pk>/editar/', views.PartidoUpdateView.as_view(), name='partido_editar'),
    path('partido/<int:pk>/eliminar/', views.PartidoDeleteView.as_view(), name='partido_eliminar'),

    # Scouting y estadísticas
    path('partido/<int:pk>/modo-partido/', views.ModoPartidoView.as_view(), name='modo_partido'),
    path('api/estadistica/registrar/', views.RegistrarAccionAPI.as_view(), name='api_registrar_estadistica'),
    path('api/registrar-cambio/', views.RegistrarCambioAPI, name='api_registrar_cambio'),
    path('api/obtener-stats-set/', views.ObtenerStatsSetAPI, name='api_obtener_stats_set'),
    path('api/stats/<int:partido_id>/<int:set_n>/', views.get_stats_json, name='api_get_stats_json'),
    path('api/estadistica/eliminar/', views.EliminarAccionAPI.as_view(), name='api_eliminar_estadistica'),
    path('api/partido/<int:partido_id>/config-set/', views.ActualizarConfigSetAPI.as_view(), name='api_actualizar_config_set'),
    path('partido/<int:pk>/stats-final/', views.PartidoStatsFinalView.as_view(), name='partido_stats_final'),
    path('partido/<int:pk>/descargar-resumen/', views.DescargarResumenPDF.as_view(), name='descargar_resumen_pdf'),
    
    # Rotaciones
    path('api/rotacion/get/<int:partido_id>/', views.GetRotacionActualAPI.as_view(), name='api_get_rotacion'),
    path('api/rotacion/inicial/<int:partido_id>/', views.GuardarAlineacionInicialAPI.as_view(), name='api_guardar_rotacion_inicial'),
    path('api/rotacion/rotar/<int:partido_id>/', views.RotarManualAPI.as_view(), name='api_rotar_manual'),
    path('api/jugadora/actualizar-posicion/', views.ActualizarPosicionJugadoraAPI.as_view(), name='api_actualizar_pos_jugadora'),
    path('api/partido/<int:partido_id>/finalizar/', views.FinalizarPartidoAPI.as_view(), name='api_finalizar_partido'),
]
