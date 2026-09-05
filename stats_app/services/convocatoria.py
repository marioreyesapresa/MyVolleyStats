"""Convocatorias por partido: guardar lista, texto WhatsApp y métricas de ficha."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from django.db import transaction

from stats_app.models import Convocatoria, ConvocatoriaJugadora, Jugadora, Partido

MOTIVOS_BAJA = ConvocatoriaJugadora.MotivoBaja
_MOTIVO_VALIDOS = {c.value for c in MOTIVOS_BAJA}


@dataclass
class LineaPantalla:
    jugadora: Jugadora
    convocada: bool
    motivo_baja: str | None


@dataclass
class StatsConvocatoriasJugadora:
    listas: int
    convocada: int
    bajas: int
    motivos: list[tuple[str, str, int]]


def plantilla_partido(partido: Partido) -> list[Jugadora]:
    return list(
        partido.equipo.jugadoras.order_by('dorsal', 'nombre', 'apellidos', 'id')
    )


def lineas_para_pantalla(partido: Partido) -> list[LineaPantalla]:
    """Plantilla actual + estado guardado. Sin lista: todas van (aún no persistido)."""
    guardadas = {}
    try:
        conv = partido.convocatoria
    except Convocatoria.DoesNotExist:
        conv = None
    if conv is not None:
        guardadas = {
            linea.jugadora_id: linea
            for linea in conv.lineas.all()
        }
    lineas = []
    for jugadora in plantilla_partido(partido):
        existente = guardadas.get(jugadora.id)
        if existente is None:
            lineas.append(LineaPantalla(jugadora=jugadora, convocada=True, motivo_baja=None))
        else:
            lineas.append(LineaPantalla(
                jugadora=jugadora,
                convocada=existente.convocada,
                motivo_baja=existente.motivo_baja if not existente.convocada else None,
            ))
    return lineas


def ids_convocadas_o_none(partido: Partido) -> set[int] | None:
    """IDs convocadas si hay lista; None = mostrar toda la plantilla."""
    try:
        conv = partido.convocatoria
    except Convocatoria.DoesNotExist:
        return None
    return set(
        conv.lineas.filter(convocada=True).values_list('jugadora_id', flat=True)
    )


def jugadoras_para_pizarra(partido: Partido):
    qs = partido.equipo.jugadoras.order_by('dorsal', 'nombre', 'apellidos', 'id')
    ids = ids_convocadas_o_none(partido)
    if ids is None:
        return qs
    return qs.filter(id__in=ids)


class ConvocatoriaInvalida(ValueError):
    pass


def _normalizar_linea(item: dict, plantilla_ids: set[int]) -> dict:
    try:
        jugadora_id = int(item.get('jugadora_id'))
    except (TypeError, ValueError):
        raise ConvocatoriaInvalida('Hay una jugadora no válida en la lista.')
    if jugadora_id not in plantilla_ids:
        raise ConvocatoriaInvalida('Hay una jugadora que no es de este equipo.')
    convocada = bool(item.get('convocada'))
    motivo = item.get('motivo_baja') or None
    if motivo == '':
        motivo = None
    if convocada:
        motivo = None
    else:
        if motivo not in _MOTIVO_VALIDOS:
            raise ConvocatoriaInvalida('Marca un motivo en cada jugadora que no va.')
    return {
        'jugadora_id': jugadora_id,
        'convocada': convocada,
        'motivo_baja': motivo,
    }


@transaction.atomic
def guardar_convocatoria(partido: Partido, payload: list[dict]) -> Convocatoria:
    plantilla = plantilla_partido(partido)
    if not plantilla:
        raise ConvocatoriaInvalida('Añade jugadoras al equipo antes de convocar.')
    plantilla_ids = {j.id for j in plantilla}
    if not payload:
        raise ConvocatoriaInvalida('La lista está vacía.')

    vistos = set()
    normalizadas = []
    for item in payload:
        linea = _normalizar_linea(item, plantilla_ids)
        if linea['jugadora_id'] in vistos:
            continue
        vistos.add(linea['jugadora_id'])
        normalizadas.append(linea)

    faltan = plantilla_ids - vistos
    if faltan:
        raise ConvocatoriaInvalida('Faltan jugadoras de la plantilla en la lista.')

    conv, _ = Convocatoria.objects.get_or_create(partido=partido)
    conv.lineas.all().delete()
    ConvocatoriaJugadora.objects.bulk_create([
        ConvocatoriaJugadora(
            convocatoria=conv,
            jugadora_id=linea['jugadora_id'],
            convocada=linea['convocada'],
            motivo_baja=linea['motivo_baja'],
        )
        for linea in normalizadas
    ])
    conv.save()
    return conv


def _etiqueta_jugadora(jugadora: Jugadora) -> str:
    dorsal = f'#{jugadora.dorsal} ' if jugadora.dorsal else ''
    return f'{dorsal}{jugadora.nombre} {jugadora.apellidos}'.strip()


def texto_whatsapp(partido: Partido, lineas: list[LineaPantalla] | None = None) -> str:
    if lineas is None:
        lineas = lineas_para_pantalla(partido)
    van = [l for l in lineas if l.convocada]
    no_van = [l for l in lineas if not l.convocada]
    fecha = partido.fecha.strftime('%d/%m/%Y')
    hora = partido.hora.strftime('%H:%M')
    sede = 'Local' if partido.local else 'Visitante'
    partes = [
        f'Convocatoria · {partido.equipo.nombre} vs {partido.rival}',
        f'{fecha} · {hora} · {sede} · {partido.lugar}',
        '',
        f'VAN ({len(van)})',
    ]
    if van:
        partes.extend(f'· {_etiqueta_jugadora(l.jugadora)}' for l in van)
    else:
        partes.append('· —')
    partes += ['', f'NO VAN ({len(no_van)})']
    if no_van:
        for l in no_van:
            motivo = dict(MOTIVOS_BAJA.choices).get(l.motivo_baja, l.motivo_baja or '—')
            partes.append(f'· {_etiqueta_jugadora(l.jugadora)} — {motivo}')
    else:
        partes.append('· —')
    return '\n'.join(partes)


def stats_convocatorias_jugadora(jugadora: Jugadora) -> StatsConvocatoriasJugadora:
    lineas = list(
        ConvocatoriaJugadora.objects
        .filter(
            jugadora=jugadora,
            convocatoria__partido__equipo=jugadora.equipo,
        )
        .only('convocada', 'motivo_baja')
    )
    listas = len(lineas)
    convocada = sum(1 for l in lineas if l.convocada)
    bajas = listas - convocada
    counts = Counter(l.motivo_baja for l in lineas if not l.convocada and l.motivo_baja)
    motivos = [
        (codigo, etiqueta, counts[codigo])
        for codigo, etiqueta in MOTIVOS_BAJA.choices
        if counts[codigo]
    ]
    return StatsConvocatoriasJugadora(
        listas=listas,
        convocada=convocada,
        bajas=bajas,
        motivos=motivos,
    )
