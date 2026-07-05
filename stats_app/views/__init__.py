from .scouting import (
    ModoPartidoView, RegistrarAccionAPI, EliminarAccionAPI, RegistrarCambioAPI, ObtenerStatsSetAPI, get_stats_json, PartidoStatsFinalView, FinalizarPartidoAPI, ActualizarConfigSetAPI
)
from .rotaciones import (
    GetRotacionActualAPI, GuardarAlineacionInicialAPI, RotarManualAPI, ActualizarPosicionJugadoraAPI
)
from .informes import (
    DescargarResumenPDF, DescargarInformeCompletoPDF
)
from .administracion import (
    DashboardView,
    EquipoCreateView, EquipoUpdateView, EquipoDeleteView, EquipoListView,
    JugadoraCreateView, JugadoraUpdateView, JugadoraDeleteView,
    PartidoCreateView, PartidoUpdateView, PartidoDeleteView
)
