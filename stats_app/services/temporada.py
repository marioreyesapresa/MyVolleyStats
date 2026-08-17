"""Estadísticas de temporada: ficha de jugadora y KPIs de equipo en Inicio.

Agrega `RegistroEstadistica` de partidos finalizados. En Inicio se reutilizan
el side-out y el break point del scout en vivo, no los complejos K1/K2 del PDF.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from stats_app.models import Equipo, Jugadora, Partido, RegistroEstadistica

from .reporting import (
    calc_breakpoint_pct,
    calc_sideout_pct,
    marcador_resumen,
    player_box_row,
)

_ROW_FIELDS = (
    'id', 'set_numero', 'jugadora_id', 'accion', 'calidad',
    'tipo_fase', 'rotacion_num', 'zona', 'zona_destino',
)


@dataclass
class StatsTemporadaEquipo:
    partidos: int
    victorias: int
    derrotas: int
    sets_local: int
    sets_rival: int
    sideout_pct: float | None
    breakpoint_pct: float | None
    ataque_pct: float | None


@dataclass
class FilaPartidoJugadora:
    partido: Partido
    stats: dict


@dataclass
class StatsJugadoraTemporada:
    totales: dict | None
    partidos: list[FilaPartidoJugadora] = field(default_factory=list)

    @property
    def partidos_count(self) -> int:
        return len(self.partidos)


def stats_temporada_equipo(equipo: Equipo) -> StatsTemporadaEquipo | None:
    """KPIs del equipo en su temporada. None si no hay scout finalizado."""
    partidos = _partidos_finalizados(equipo)
    if not partidos:
        return None
    _precargar_filas(partidos)

    con_scout = 0
    victorias = derrotas = 0
    sets_local = sets_rival = 0
    sideouts: list[float] = []
    breakpoints: list[float] = []
    ataques: list[dict] = []
    for partido in partidos:
        resumen = marcador_resumen(partido)
        if not resumen['tiene_scout']:
            continue
        con_scout += 1
        sets_local += resumen['sets_local']
        sets_rival += resumen['sets_rival']
        if resumen['victoria'] is True:
            victorias += 1
        elif resumen['victoria'] is False:
            derrotas += 1
        sideout = calc_sideout_pct(partido, None)
        if sideout is not None:
            sideouts.append(sideout)
        breakpoint = calc_breakpoint_pct(partido, None)
        if breakpoint is not None:
            breakpoints.append(breakpoint)
        ataques.extend(
            r for r in getattr(partido, '_reporting_rows_cache', [])
            if r['accion'] == 'ATAQUE'
        )

    if con_scout == 0:
        return None
    return StatsTemporadaEquipo(
        partidos=con_scout,
        victorias=victorias,
        derrotas=derrotas,
        sets_local=sets_local,
        sets_rival=sets_rival,
        sideout_pct=_media(sideouts),
        breakpoint_pct=_media(breakpoints),
        ataque_pct=_eficacia_ataque(ataques),
    )


def stats_jugadora_temporada(jugadora: Jugadora) -> StatsJugadoraTemporada:
    """Totales y desglose por partido finalizado con acciones de scout."""
    partidos = _partidos_finalizados(jugadora.equipo)
    _precargar_filas(partidos)

    filas: list[FilaPartidoJugadora] = []
    todas: list[dict] = []
    for partido in partidos:
        rows = [
            r for r in getattr(partido, '_reporting_rows_cache', [])
            if r.get('jugadora_id') == jugadora.id
        ]
        box = player_box_row(jugadora, rows)
        if box is None:
            continue
        todas.extend(rows)
        filas.append(FilaPartidoJugadora(partido=partido, stats=_resumen_jugadora(box)))

    totales_box = player_box_row(jugadora, todas) if todas else None
    totales = None
    if totales_box is not None:
        totales = _resumen_jugadora(totales_box)
        totales['partidos'] = len(filas)
    return StatsJugadoraTemporada(totales=totales, partidos=filas)


def _resumen_jugadora(box: dict) -> dict:
    return {
        'ataque': box['ataque_kills'],
        'bloqueo': box['bloqueo_pts'],
        'saque': box['saque_aces'],
        'recepcion_pct': box['recepcion_pct'],
        'balance': box['balance'],
        'puntos': box['puntos'],
        'errores': box['errores'],
    }


def _partidos_finalizados(equipo: Equipo) -> list[Partido]:
    return list(
        equipo.partidos.filter(finalizado=True).order_by('fecha', 'hora', 'id')
    )


def _precargar_filas(partidos: list[Partido]) -> None:
    if not partidos:
        return
    agrupadas: dict[int, list] = defaultdict(list)
    for row in (
        RegistroEstadistica.objects
        .filter(partido_id__in=[p.id for p in partidos])
        .order_by('id')
        .values('partido_id', *_ROW_FIELDS)
    ):
        agrupadas[row['partido_id']].append(row)
    for partido in partidos:
        partido._reporting_rows_cache = agrupadas.get(partido.id, [])


def _eficacia_ataque(ataques: list[dict]) -> float | None:
    if not ataques:
        return None
    puntos = sum(1 for r in ataques if r['calidad'] == '++')
    errores = sum(1 for r in ataques if r['calidad'] == '--')
    return round((puntos - errores) / len(ataques) * 100, 1)


def _media(valores: list[float]) -> float | None:
    if not valores:
        return None
    return round(sum(valores) / len(valores), 1)
