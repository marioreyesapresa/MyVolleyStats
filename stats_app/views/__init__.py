from .scouting import (
    ModoPartidoView, RegistrarAccionAPI, EliminarAccionAPI, RegistrarCambioAPI, ObtenerStatsSetAPI, get_stats_json, PartidoStatsFinalView, PartidoStatsAvanzadoView, FinalizarPartidoAPI, ActualizarConfigSetAPI
)
from .rotaciones import (
    GetRotacionActualAPI, GuardarAlineacionInicialAPI, RotarManualAPI, ActualizarPosicionJugadoraAPI
)
from .informes import (
    DescargarResumenPDF, DescargarInformeCompletoPDF, DescargarInformeAvanzadoPDF
)
from .administracion import (
    ConfiguracionView, DashboardView,
    EquipoCreateView, EquipoUpdateView, EquipoDeleteView, EquipoListView,
    JugadoraCreateView, JugadoraUpdateView, JugadoraDeleteView,
    PartidoCreateView, PartidoUpdateView, PartidoDeleteView
)
