"""Importar y exportar la plantilla de un equipo en CSV.

Acepta el CSV simple (dorsal,nombre,apellidos,posicion) y los Excel del club
exportados desde Google Sheets: informe de jugadoras y hoja de asistencia.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from django.db import transaction
from django.utils.text import slugify

from ..models import Equipo, Jugadora

CSV_HEADERS = ('dorsal', 'nombre', 'apellidos', 'posicion')
MAX_FILAS = 40
MAX_BYTES = 64 * 1024
MAX_HEADER_SCAN = 15
DORSAL_MIN = 1
DORSAL_MAX = 99

POSICION_ALIASES = {
    'COLOCADORA': 'COLOCADORA',
    'COL': 'COLOCADORA',
    'CO': 'COLOCADORA',
    'OPUESTA': 'OPUESTA',
    'OPU': 'OPUESTA',
    'O': 'OPUESTA',
    'CENTRAL': 'CENTRAL',
    'CEN': 'CENTRAL',
    'C': 'CENTRAL',
    'RECEPTORA': 'RECEPTORA',
    'REC': 'RECEPTORA',
    'R': 'RECEPTORA',
    'LIBERO': 'LIBERO',
    'LÍBERO': 'LIBERO',
    'LIB': 'LIBERO',
    'L': 'LIBERO',
}


@dataclass
class FilaPlantilla:
    linea: int
    dorsal: int | None
    nombre: str
    apellidos: str
    posicion: str | None
    fecha_nacimiento: date | None = None


@dataclass
class ParseoPlantilla:
    filas: list[FilaPlantilla] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    sin_dorsal: int = 0


@dataclass
class ResultadoImportacion:
    creadas: int = 0
    actualizadas: int = 0
    errores: list[str] = field(default_factory=list)


def exportar_plantilla_csv(equipo: Equipo) -> bytes:
    """CSV con BOM para que Excel respete tildes."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=',', lineterminator='\n')
    writer.writerow(CSV_HEADERS)
    for j in equipo.jugadoras.order_by('dorsal', 'nombre'):
        writer.writerow([
            j.dorsal if j.dorsal is not None else '',
            j.nombre or '',
            j.apellidos or '',
            j.posicion or '',
        ])
    return buf.getvalue().encode('utf-8-sig')


def nombre_archivo_csv(equipo: Equipo) -> str:
    slug = slugify(equipo.nombre) or 'equipo'
    return f'plantilla-{slug}.csv'


def parsear_plantilla_csv(contenido: bytes) -> ParseoPlantilla:
    resultado = ParseoPlantilla()
    if not contenido:
        resultado.errores.append('El archivo está vacío.')
        return resultado
    if len(contenido) > MAX_BYTES:
        resultado.errores.append('El archivo es demasiado grande (máx. 64 KB).')
        return resultado

    try:
        texto = contenido.decode('utf-8-sig')
    except UnicodeDecodeError:
        resultado.errores.append('No se pudo leer el archivo. Guárdalo como CSV UTF-8.')
        return resultado

    texto = texto.replace('\r\n', '\n').replace('\r', '\n')
    if not texto.strip():
        resultado.errores.append('El archivo está vacío.')
        return resultado

    delimiter = _detectar_separador(texto)
    filas_raw = list(csv.reader(io.StringIO(texto), delimiter=delimiter))
    cabecera_idx, indices = _encontrar_cabecera(filas_raw)
    if indices is None:
        resultado.errores.append(
            'No encuentro columnas de nombre y apellidos. '
            'Vale el CSV simple (dorsal,nombre,apellidos,posicion) '
            'o el Excel del club (Nombre / Apellido / Dorsal o Nº).'
        )
        return resultado

    vistos_dorsal: dict[int, int] = {}
    vistos_nombre: set[str] = set()
    datos = 0
    for offset, raw in enumerate(filas_raw[cabecera_idx + 1:], start=cabecera_idx + 2):
        if not raw or all(not (c or '').strip() for c in raw):
            continue
        datos += 1
        if datos > MAX_FILAS:
            resultado.errores.append(f'Solo se importan las primeras {MAX_FILAS} filas.')
            break

        def col(campo: str) -> str:
            idx = indices.get(campo)
            if idx is None or idx >= len(raw):
                return ''
            return (raw[idx] or '').strip()

        linea_errores = []
        dorsal_txt = col('dorsal')
        nombre = col('nombre')
        apellidos = col('apellidos')
        posicion_txt = col('posicion')
        fecha_txt = col('fecha_nacimiento') or col('anio')

        dorsal = _parsear_dorsal(dorsal_txt) if dorsal_txt else None
        if dorsal_txt and dorsal is None:
            linea_errores.append(f'Fila {offset}: dorsal «{dorsal_txt}» no válido.')
        elif dorsal is not None and dorsal in vistos_dorsal:
            linea_errores.append(f'Fila {offset}: dorsal #{dorsal} duplicado en el archivo.')

        if not nombre:
            linea_errores.append(f'Fila {offset}: falta el nombre.')
        elif len(nombre) > 100:
            linea_errores.append(f'Fila {offset}: el nombre es demasiado largo.')

        if len(apellidos) > 150:
            linea_errores.append(f'Fila {offset}: los apellidos son demasiado largos.')

        clave_nom = _clave_nombre(nombre, apellidos) if nombre else ''
        if clave_nom and clave_nom in vistos_nombre:
            linea_errores.append(f'Fila {offset}: {nombre} {apellidos} está duplicada en el archivo.')

        posicion = None
        if posicion_txt:
            posicion = normalizar_posicion(posicion_txt)
            if posicion is None:
                linea_errores.append(f'Fila {offset}: posición «{posicion_txt}» no válida.')

        if linea_errores:
            resultado.errores.extend(linea_errores)
            continue

        if dorsal is not None:
            vistos_dorsal[dorsal] = offset
        if clave_nom:
            vistos_nombre.add(clave_nom)
        if dorsal is None:
            resultado.sin_dorsal += 1
        resultado.filas.append(FilaPlantilla(
            linea=offset,
            dorsal=dorsal,
            nombre=nombre,
            apellidos=apellidos,
            posicion=posicion,
            fecha_nacimiento=_parsear_fecha(fecha_txt),
        ))

    return resultado


def normalizar_posicion(valor: str) -> str | None:
    clave = (valor or '').strip().upper()
    clave = re.sub(r'\s+', '', clave)
    return POSICION_ALIASES.get(clave)


def aplicar_plantilla(equipo: Equipo, filas: list[FilaPlantilla]) -> ResultadoImportacion:
    resultado = ResultadoImportacion()
    if not filas:
        return resultado

    jugadoras = list(equipo.jugadoras.all())
    por_dorsal = {j.dorsal: j for j in jugadoras if j.dorsal is not None}
    por_nombre = {_clave_nombre(j.nombre, j.apellidos): j for j in jugadoras}

    with transaction.atomic():
        for fila in filas:
            clave = _clave_nombre(fila.nombre, fila.apellidos)
            jugadora = None
            if fila.dorsal is not None:
                jugadora = por_dorsal.get(fila.dorsal)
            if jugadora is None:
                jugadora = por_nombre.get(clave)

            if jugadora is None:
                jugadora = Jugadora.objects.create(
                    equipo=equipo,
                    dorsal=fila.dorsal,
                    nombre=fila.nombre,
                    apellidos=fila.apellidos,
                    posicion=fila.posicion,
                    fecha_nacimiento=fila.fecha_nacimiento,
                )
                resultado.creadas += 1
            else:
                jugadora.nombre = fila.nombre
                jugadora.apellidos = fila.apellidos
                campos = ['nombre', 'apellidos']
                if fila.dorsal is not None:
                    jugadora.dorsal = fila.dorsal
                    campos.append('dorsal')
                if fila.posicion is not None:
                    jugadora.posicion = fila.posicion
                    campos.append('posicion')
                if fila.fecha_nacimiento is not None and not jugadora.fecha_nacimiento:
                    jugadora.fecha_nacimiento = fila.fecha_nacimiento
                    campos.append('fecha_nacimiento')
                jugadora.save(update_fields=campos)
                resultado.actualizadas += 1

            if jugadora.dorsal is not None:
                por_dorsal[jugadora.dorsal] = jugadora
            por_nombre[_clave_nombre(jugadora.nombre, jugadora.apellidos)] = jugadora
    return resultado


def _encontrar_cabecera(filas_raw: list[list[str]]) -> tuple[int | None, dict[str, int] | None]:
    tope = min(len(filas_raw), MAX_HEADER_SCAN)
    for i in range(tope):
        indices = _indices_columnas([_norm_celda(c) for c in filas_raw[i]])
        if indices is not None:
            return i, indices
    return None, None


def _indices_columnas(header: list[str]) -> dict[str, int] | None:
    indices: dict[str, int] = {}
    for i, bruto in enumerate(header):
        campo = _tipo_columna(bruto)
        if campo == 'nombre_apellidos_span':
            if 'nombre' not in indices:
                indices['nombre'] = i
            if 'apellidos' not in indices and i + 1 < len(header):
                siguiente = _tipo_columna(header[i + 1])
                if siguiente is None or siguiente == 'nombre_apellidos_span':
                    indices['apellidos'] = i + 1
            continue
        if campo and campo not in indices:
            indices[campo] = i
    if 'nombre' in indices and 'apellidos' in indices:
        return indices
    return None


def _tipo_columna(valor: str) -> str | None:
    n = _norm_celda(valor)
    n = n.split('(')[0].strip()
    n = re.sub(r'\s+', ' ', n)
    if not n:
        return None
    if n in {'dorsal', 'nº', 'n°', 'nº.', 'numero', 'número', 'num'}:
        return 'dorsal'
    if n in {'nombre', 'nombres'}:
        return 'nombre'
    if n in {'apellido', 'apellidos'}:
        return 'apellidos'
    if n in {'posicion', 'posición', 'puesto'}:
        return 'posicion'
    if n in {'ano', 'anio', 'año'}:
        return 'anio'
    if n in {'fecha nac', 'fecha nacimiento', 'nacimiento', 'f. nac', 'fnac'}:
        return 'fecha_nacimiento'
    if 'apellidos y nombre' in n or 'nombre y apellidos' in n:
        return 'nombre_apellidos_span'
    return None


def _detectar_separador(texto: str) -> str:
    candidatas = [linea for linea in texto.split('\n') if linea.strip()][:MAX_HEADER_SCAN]
    for cabecera in candidatas:
        if cabecera.count(';') > cabecera.count(','):
            return ';'
        if cabecera.count(',') > 0:
            return ','
    muestra = texto[:4096]
    try:
        return csv.Sniffer().sniff(muestra, delimiters=',;').delimiter
    except csv.Error:
        return ','


def _norm_celda(valor: str) -> str:
    texto = (valor or '').replace('\ufeff', '').strip().lower()
    texto = ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )
    return texto


def _clave_nombre(nombre: str, apellidos: str) -> str:
    return f'{_norm_celda(nombre)}|{_norm_celda(apellidos)}'


def _parsear_dorsal(valor: str) -> int | None:
    if not valor:
        return None
    try:
        dorsal = int(valor)
    except (TypeError, ValueError):
        return None
    if dorsal < DORSAL_MIN or dorsal > DORSAL_MAX:
        return None
    return dorsal


def _parsear_fecha(valor: str) -> date | None:
    texto = (valor or '').strip()
    if not texto:
        return None
    if re.fullmatch(r'\d{4}', texto):
        anio = int(texto)
        if 1980 <= anio <= date.today().year:
            return date(anio, 1, 1)
        return None
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y'):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None
