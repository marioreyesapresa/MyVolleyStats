"""Agregación de estadísticas para informes en vivo y post-partido.

Rendimiento
-----------
Todas las funciones de este módulo parten de una única consulta a BD por
partido (`_get_match_rows`), cacheada en la propia instancia de `Partido`
mientras dure la petición. El resto de cálculos (por set, por jugadora, por
zona, por rotación...) se hacen en memoria sobre esa lista de filas.

Esto evita el patrón N+1 que tenía la versión anterior (una `.count()` por
cada combinación de set × jugadora × fundamento × calidad), que en un
partido de varios sets podía disparar miles de consultas y bloquear la
generación del informe completo, especialmente contra una BD remota
(Neon) con latencia de red por consulta.
"""

from collections import defaultdict

from stats_app.models import Jugadora, RegistroEstadistica, RotacionSet

FUNDAMENTOS = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
FUNDAMENTOS_SCOUT = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']

_ACCIONES_PUNTO_LOCAL = {'SAQUE', 'ATAQUE', 'BLOQUEO'}
_ROTACIONES_CAMPOS = [
    'pos1_id', 'pos2_id', 'pos3_id', 'pos4_id', 'pos5_id', 'pos6_id',
    'libero1_id', 'libero2_id',
]


# ── Carga y caché de datos por partido ───────────────────────────────────
# Todo lo que sigue evita re-consultar la BD dentro de la misma petición:
# el objeto `partido` vive durante toda la generación del informe y todas
# las funciones de este módulo reciben la misma instancia, así que cachear
# en un atributo del propio objeto es seguro y no requiere tocar las firmas
# públicas (usadas también directamente en tests).

def _get_match_rows(partido):
    cached = getattr(partido, '_reporting_rows_cache', None)
    if cached is not None:
        return cached
    rows = list(
        RegistroEstadistica.objects.filter(partido=partido)
        .order_by('id')
        .values('id', 'set_numero', 'jugadora_id', 'accion', 'calidad', 'tipo_fase', 'rotacion_num', 'zona')
    )
    partido._reporting_rows_cache = rows
    return rows


def _rows_by_set(partido):
    cached = getattr(partido, '_reporting_rows_by_set_cache', None)
    if cached is not None:
        return cached
    grouped = defaultdict(list)
    for r in _get_match_rows(partido):
        grouped[r['set_numero']].append(r)
    partido._reporting_rows_by_set_cache = grouped
    return grouped


def _rows_for(partido, set_num):
    """Filas del partido, de un set concreto o de todos si `set_num` es None."""
    if set_num is None:
        return _get_match_rows(partido)
    return _rows_by_set(partido).get(int(set_num), [])


def _rotaciones_por_set(partido):
    cached = getattr(partido, '_reporting_rotaciones_cache', None)
    if cached is not None:
        return cached
    grouped = defaultdict(list)
    for rot in RotacionSet.objects.filter(partido=partido):
        grouped[rot.set_numero].append(rot)
    partido._reporting_rotaciones_cache = grouped
    return grouped


def _jugadoras_map(partido):
    """Todas las jugadoras que aparecen en el partido (acciones o rotaciones),
    indexadas por id, en una única consulta reutilizada para todos los sets.
    """
    cached = getattr(partido, '_reporting_jugadoras_cache', None)
    if cached is not None:
        return cached
    ids = {r['jugadora_id'] for r in _get_match_rows(partido) if r['jugadora_id'] is not None}
    for rots in _rotaciones_por_set(partido).values():
        for rot in rots:
            for field in _ROTACIONES_CAMPOS:
                val = getattr(rot, field)
                if val:
                    ids.add(val)
    mapa = {j.id: j for j in Jugadora.objects.filter(id__in=ids)}
    partido._reporting_jugadoras_cache = mapa
    return mapa


def invalidar_cache_reporting(partido):
    """Limpia la caché de reporting de la instancia (por si se reutiliza tras
    escribir nuevos registros con el mismo objeto `partido` en memoria)."""
    for attr in (
        '_reporting_rows_cache', '_reporting_rows_by_set_cache',
        '_reporting_rotaciones_cache', '_reporting_jugadoras_cache',
    ):
        if hasattr(partido, attr):
            delattr(partido, attr)


# ── Cálculos base sobre filas en memoria ─────────────────────────────────

def _es_punto_local(r):
    return (r['accion'] in _ACCIONES_PUNTO_LOCAL and r['calidad'] == '++') or r['accion'] == 'ERROR_RIVAL'


def _es_punto_rival(r):
    return r['accion'] == 'PUNTO_RIVAL' or r['calidad'] == '--'


def calc_set_score(partido, set_num):
    rows = _rows_for(partido, set_num)
    local = sum(1 for r in rows if _es_punto_local(r))
    rival = sum(1 for r in rows if _es_punto_rival(r))
    return local, rival


def count_sets_won(partido):
    """Sets ganados por cada equipo según marcadores cerrados en BD."""
    sets_local = sets_rival = 0
    for s in _rows_by_set(partido).keys():
        p_l, p_r = calc_set_score(partido, s)
        limit = partido.limite_puntos_set(s)
        if (p_l >= limit or p_r >= limit) and abs(p_l - p_r) >= 2:
            if p_l > p_r:
                sets_local += 1
            else:
                sets_rival += 1
    return sets_local, sets_rival


def detect_set_activo(partido):
    """Primer set sin cerrar; si todos están cerrados, el último con datos."""
    sets_nums = sorted(_rows_by_set(partido).keys())
    if not sets_nums:
        return 1
    for s in sets_nums:
        p_l, p_r = calc_set_score(partido, s)
        limit = partido.limite_puntos_set(s)
        if not ((p_l >= limit or p_r >= limit) and abs(p_l - p_r) >= 2):
            return s
    return max(sets_nums)


def get_sets_con_datos(partido):
    """Lista ordenada de números de set con al menos un registro."""
    return sorted(_rows_by_set(partido).keys())


def build_partido_snapshot(partido):
    """Marcador y set activo desde BD para pintar la UI sin esperar al fetch."""
    set_activo = detect_set_activo(partido)
    p_local, p_rival = calc_set_score(partido, set_activo)
    sets_local, sets_rival = count_sets_won(partido)
    return {
        'set_activo': set_activo,
        'puntos_local': p_local,
        'puntos_rival': p_rival,
        'sets_local': sets_local,
        'sets_rival': sets_rival,
        'sets_con_datos': get_sets_con_datos(partido),
    }


def merito_y_error_rival(partido, set_num):
    """(puntos de mérito propio en saque/ataque/bloqueo, errores forzados al rival)."""
    rows = _rows_for(partido, set_num)
    merito = sum(1 for r in rows if r['accion'] in _ACCIONES_PUNTO_LOCAL and r['calidad'] == '++')
    err_rival = sum(1 for r in rows if r['accion'] == 'ERROR_RIVAL')
    return merito, err_rival


def origen_puntos_totales(partido, set_num=None):
    """Desglose de puntos propios por fundamento + errores forzados (gráfico
    'Origen de los puntos')."""
    rows = _rows_for(partido, set_num)
    return {
        'Ataque': sum(1 for r in rows if r['accion'] == 'ATAQUE' and r['calidad'] == '++'),
        'Saque': sum(1 for r in rows if r['accion'] == 'SAQUE' and r['calidad'] == '++'),
        'Bloqueo': sum(1 for r in rows if r['accion'] == 'BLOQUEO' and r['calidad'] == '++'),
        'Error Rival': sum(1 for r in rows if r['accion'] == 'ERROR_RIVAL'),
    }


def _phase_efficiency(rows, fases, acciones=None):
    fases_set = set(fases)
    phase_rows = [r for r in rows if r['tipo_fase'] in fases_set]
    if acciones:
        acciones_set = set(acciones)
        phase_rows = [r for r in phase_rows if r['accion'] in acciones_set]
    wins = sum(1 for r in phase_rows if _es_punto_local(r))
    losses = sum(1 for r in phase_rows if _es_punto_rival(r))
    total = wins + losses
    if total == 0:
        return None
    return round(wins / total * 100, 1)


def calc_sideout_pct(partido, set_num):
    """% de side-out cuando recibimos (fases K1/K2)."""
    rows = _rows_for(partido, set_num)
    pct = _phase_efficiency(rows, ['K1', 'K2'])
    if pct is not None:
        return pct
    rec = [r for r in rows if r['accion'] == 'RECEPCION']
    total = len(rec)
    if total == 0:
        return None
    positivos = sum(1 for r in rec if r['calidad'] in ('++', '+'))
    return round(positivos / total * 100, 1)


def calc_breakpoint_pct(partido, set_num):
    """% de puntos ganados con nuestro saque (fase K0)."""
    rows = _rows_for(partido, set_num)
    return _phase_efficiency(rows, ['K0'])


def calc_rival_sideout_pct(partido, set_num):
    """Aproximación del side-out rival cuando nosotros sacamos (K0)."""
    rows = _rows_for(partido, set_num)
    k0 = [r for r in rows if r['tipo_fase'] == 'K0']
    rival_wins = sum(1 for r in k0 if _es_punto_rival(r))
    our_wins = sum(1 for r in k0 if _es_punto_local(r))
    total = rival_wins + our_wins
    if total == 0:
        return None
    return round(rival_wins / total * 100, 1)


def _fund_counts(rows, accion):
    pp = p = eq = m = mm = 0
    for r in rows:
        if r['accion'] != accion:
            continue
        cal = r['calidad']
        if cal == '++':
            pp += 1
        elif cal == '+':
            p += 1
        elif cal == '=':
            eq += 1
        elif cal == '-':
            m += 1
        elif cal == '--':
            mm += 1
    total = pp + p + eq + m + mm
    return {'pp': pp, 'p': p, 'eq': eq, 'm': m, 'mm': mm, 'total': total}


def player_box_row(jugadora, rows_jugadora):
    """`rows_jugadora`: filas del set ya filtradas a esta jugadora (cualquier
    acción); aquí se restringe a los fundamentos de scouting."""
    fund_set = set(FUNDAMENTOS_SCOUT)
    j_rows = [r for r in rows_jugadora if r['accion'] in fund_set]
    if not j_rows:
        return None

    saque = _fund_counts(j_rows, 'SAQUE')
    rec = _fund_counts(j_rows, 'RECEPCION')
    col = _fund_counts(j_rows, 'COLOCACION')
    atq = _fund_counts(j_rows, 'ATAQUE')
    blo = _fund_counts(j_rows, 'BLOQUEO')
    defn = _fund_counts(j_rows, 'DEFENSA')

    puntos = sum(1 for r in j_rows if r['calidad'] == '++')
    errores = sum(1 for r in j_rows if r['calidad'] == '--')
    balance = puntos - errores
    acciones = len(j_rows)

    swings = atq['total']
    kills = atq['pp']
    hit_err = atq['mm']
    hit_pct = round((kills - hit_err) / swings, 3) if swings > 0 else None

    rec_pos = rec['pp'] + rec['p']
    def_pos = defn['pp'] + defn['p']
    rec_pct = round(rec_pos / rec['total'] * 100, 1) if rec['total'] > 0 else None

    return {
        'id': jugadora.id,
        'dorsal': jugadora.dorsal,
        'nombre': jugadora.nombre,
        'acciones': acciones,
        'balance': balance,
        'puntos': puntos,
        'errores': errores,
        'scored_minus_err': balance,
        'ataque_swings': swings,
        'ataque_kills': kills,
        'ataque_err': hit_err,
        'ataque_pct': hit_pct,
        'bloqueo_pts': blo['pp'],
        'bloqueo_toques': blo['p'] + blo['eq'],
        'bloqueo_err': blo['mm'],
        'asistencias': col['pp'] + col['p'] + col['eq'],
        'colocacion_err': col['mm'],
        'defensas': defn['pp'] + defn['p'] + defn['eq'],
        'defensa_err': defn['mm'],
        'recepciones': rec['total'],
        'recepcion_pos': rec_pos,
        'recepcion_err': rec['mm'],
        'recepcion_pct': rec_pct,
        'control_balon_pos': rec_pos + def_pos,
        'control_balon_err': rec['mm'] + defn['mm'],
        'saques': saque['total'],
        'saque_aces': saque['pp'],
        'saque_err': saque['mm'],
        'efi_global': round(max(0, balance / acciones * 100), 1) if acciones else 0,
    }


def _jugadoras_en_set(partido, set_num):
    rows = _rows_for(partido, set_num)
    ids = {r['jugadora_id'] for r in rows if r['jugadora_id'] is not None}
    for rot in _rotaciones_por_set(partido).get(int(set_num), []):
        for field in _ROTACIONES_CAMPOS:
            val = getattr(rot, field)
            if val:
                ids.add(val)
    mapa = _jugadoras_map(partido)
    jugadoras = [mapa[i] for i in ids if i in mapa]
    jugadoras.sort(key=lambda j: (j.dorsal is None, j.dorsal))
    return jugadoras


def _totals_row(players):
    if not players:
        return None
    keys = [
        'acciones', 'balance', 'puntos', 'errores', 'scored_minus_err',
        'ataque_swings', 'ataque_kills', 'ataque_err',
        'bloqueo_pts', 'bloqueo_toques', 'bloqueo_err',
        'asistencias', 'colocacion_err', 'defensas', 'defensa_err',
        'recepciones', 'recepcion_err', 'saques', 'saque_aces', 'saque_err',
    ]
    totals = {k: sum(p.get(k, 0) or 0 for p in players) for k in keys}
    swings = totals['ataque_swings']
    totals['ataque_pct'] = round((totals['ataque_kills'] - totals['ataque_err']) / swings, 3) if swings else None
    rec = totals['recepciones']
    if rec > 0:
        rec_pos = sum(
            (p.get('recepciones', 0) - p.get('recepcion_err', 0))
            for p in players
        )
        totals['recepcion_pct'] = round(rec_pos / rec * 100, 1)
    else:
        totals['recepcion_pct'] = None
    totals['nombre'] = 'TOTAL EQUIPO'
    totals['dorsal'] = ''
    totals['efi_global'] = round(max(0, totals['balance'] / totals['acciones'] * 100), 1) if totals['acciones'] else 0
    return totals


def build_set_report(partido, set_num):
    rows_set = _rows_for(partido, set_num)
    local, rival = calc_set_score(partido, set_num)

    rows_by_jugadora = defaultdict(list)
    for r in rows_set:
        if r['jugadora_id'] is not None:
            rows_by_jugadora[r['jugadora_id']].append(r)

    players = []
    for j in _jugadoras_en_set(partido, set_num):
        row = player_box_row(j, rows_by_jugadora.get(j.id, []))
        if row:
            players.append(row)
    players.sort(key=lambda x: (-x['balance'], -x['puntos']))
    return {
        'set_num': set_num,
        'score_local': local,
        'score_rival': rival,
        'score': f'{local}–{rival}',
        'sideout_pct': calc_sideout_pct(partido, set_num),
        'breakpoint_pct': calc_breakpoint_pct(partido, set_num),
        'rival_sideout_pct': calc_rival_sideout_pct(partido, set_num),
        'jugadoras': players,
        'totales': _totals_row(players),
    }


def build_quick_set_report(partido, set_num):
    report = build_set_report(partido, set_num)
    # Subconjunto para tabla rápida en banquillo
    report['tabla_rapida'] = [
        {
            'id': p['id'],
            'dorsal': p['dorsal'],
            'nombre': p['nombre'],
            'balance': p['balance'],
            'puntos': p['puntos'],
            'errores': p['errores'],
            'ataque_kills': p['ataque_kills'],
            'ataque_err': p['ataque_err'],
            'ataque_pct': p['ataque_pct'],
            'recepciones': p['recepciones'],
            'recepcion_err': p['recepcion_err'],
            'saque_aces': p['saque_aces'],
            'saque_err': p['saque_err'],
            'bloqueo_pts': p['bloqueo_pts'],
            'asistencias': p['asistencias'],
            'colocacion_err': p['colocacion_err'],
            'defensas': p['defensas'],
            'defensa_err': p['defensa_err'],
            'alerta': _candidata_cambio(p),
        }
        for p in report['jugadoras']
    ]
    return report


def _alto_volumen_buen_ratio(positivos, errores, min_toques=10, min_ratio=0.80):
    """True si hay volumen suficiente y la tasa de acierto supera el umbral."""
    positivos = positivos or 0
    errores = errores or 0
    total = positivos + errores
    if total < min_toques:
        return False
    return positivos / total >= min_ratio


def _candidata_cambio(p):
    """Fila roja en banquillo: sustitución sugerida, sin castigar roles defensivos eficaces."""
    if p.get('asistencias', 0) >= 5 and p.get('colocacion_err', 0) == 0:
        return p['balance'] < -3

    if _alto_volumen_buen_ratio(p.get('defensas'), p.get('defensa_err')):
        return p['balance'] <= -3
    if _alto_volumen_buen_ratio(p.get('recepcion_pos'), p.get('recepcion_err')):
        return p['balance'] <= -3

    if p['puntos'] == 0 and p['errores'] >= 2:
        return True
    return p['balance'] <= -3


def _player_destacado(p, detalle):
    return {
        'id': p['id'],
        'dorsal': p['dorsal'],
        'nombre': p['nombre'],
        'detalle': detalle,
    }


def _eficacia_recepcion(p):
    total = p.get('recepciones') or 0
    if total == 0:
        return None
    err = p.get('recepcion_err') or 0
    return (total - err) / total


def _eficacia_defensa(p):
    buenas = p.get('defensas') or 0
    err = p.get('defensa_err') or 0
    total = buenas + err
    if total == 0:
        return None
    return buenas / total


def _mejor_fundamento_volumen_min_eficacia(
    players, volumen_fn, eficacia_fn, detalle_fn, min_eficacia=0.80,
):
    """Entre jugadoras con eficacia >= umbral, gana la de mayor volumen."""
    candidatas = []
    for p in players:
        vol = volumen_fn(p)
        efic = eficacia_fn(p)
        if vol < 1 or efic is None or efic < min_eficacia:
            continue
        candidatas.append((vol, efic, p))
    if not candidatas:
        return None
    top = max(candidatas, key=lambda x: (x[0], x[1]))[2]
    return _player_destacado(top, detalle_fn(top))


def calc_k1_complex_pct(partido, set_num):
    """Calidad del complejo recepción+ataque: (++ − −−) / total acciones."""
    rows = _rows_for(partido, set_num)
    acciones = {'RECEPCION', 'ATAQUE'}
    pp = mm = total = 0
    for r in rows:
        if r['accion'] in acciones:
            total += 1
            if r['calidad'] == '++':
                pp += 1
            elif r['calidad'] == '--':
                mm += 1
    if total == 0:
        return 0
    return round(max(0, ((pp - mm) / total) * 100))


def calc_k2_complex_pct(partido, set_num):
    """Calidad del complejo saque+bloqueo+defensa: (++ − −−) / total acciones."""
    rows = _rows_for(partido, set_num)
    acciones = {'SAQUE', 'BLOQUEO', 'DEFENSA'}
    pp = mm = total = 0
    for r in rows:
        if r['accion'] in acciones:
            total += 1
            if r['calidad'] == '++':
                pp += 1
            elif r['calidad'] == '--':
                mm += 1
    if total == 0:
        return 0
    return round(max(0, ((pp - mm) / total) * 100))


def _leaders_from_players(players):
    """Tres líderes independientes a partir de filas de jugadora."""
    if not players:
        return {'estrella': None, 'maxima_anotadora': None, 'mejor_saque': None}

    estrella = max(players, key=lambda p: (p['balance'], p['puntos']))
    estrella_out = None
    if estrella.get('acciones', 1) > 0:
        sign = '+' if estrella['balance'] > 0 else ''
        estrella_out = _player_destacado(estrella, f"{sign}{estrella['balance']} saldo")

    max_anot = max(players, key=lambda p: (p['ataque_kills'], p.get('ataque_pct') or -1))
    max_anot_out = None
    if max_anot['ataque_kills'] > 0:
        max_anot_out = _player_destacado(max_anot, f"{max_anot['ataque_kills']} pts")

    mejor_srv = max(players, key=lambda p: p['saque_aces'])
    mejor_srv_out = None
    if mejor_srv['saque_aces'] > 0:
        aces = mejor_srv['saque_aces']
        label = f"{aces} ace" if aces == 1 else f"{aces} aces"
        mejor_srv_out = _player_destacado(mejor_srv, label)

    return {
        'estrella': estrella_out,
        'maxima_anotadora': max_anot_out,
        'mejor_saque': mejor_srv_out,
    }


def _peor_errores(players, key_err, min_err=1):
    candidatas = [p for p in players if (p.get(key_err) or 0) >= min_err]
    if not candidatas:
        return None
    worst = max(candidatas, key=lambda p: p.get(key_err) or 0)
    err = worst[key_err]
    label = f"{err} err" if err == 1 else f"{err} errores"
    return _player_destacado(worst, label)


def _destacados_from_players(players, min_ataques=3):
    """Cara y cruz por fundamento (ataque, recepción, saque, bloqueo, defensa)."""
    mejor_ataque = None
    with_kills = [p for p in players if p['ataque_kills'] > 0]
    max_kills = max(with_kills, key=lambda p: p['ataque_kills']) if with_kills else None
    candidatas_eff = [
        p for p in players
        if p['ataque_swings'] >= min_ataques and p.get('ataque_pct') is not None
    ]
    best_eff = max(candidatas_eff, key=lambda p: p['ataque_pct']) if candidatas_eff else None

    if max_kills and (
        not best_eff
        or max_kills['ataque_kills'] > best_eff['ataque_kills']
        or (
            max_kills['ataque_kills'] == best_eff['ataque_kills']
            and (max_kills.get('ataque_pct') or -1) >= (best_eff.get('ataque_pct') or -1)
        )
    ):
        mejor_ataque = _player_destacado(max_kills, f"{max_kills['ataque_kills']} pts")
    elif best_eff:
        pct = round(best_eff['ataque_pct'] * 100)
        mejor_ataque = _player_destacado(
            best_eff,
            f"{pct}% ({best_eff['ataque_kills']}/{best_eff['ataque_err']})",
        )

    peor_ataque = _peor_errores(players, 'ataque_err')

    mejor_recepcion = _mejor_fundamento_volumen_min_eficacia(
        players,
        volumen_fn=lambda p: p.get('recepciones') or 0,
        eficacia_fn=_eficacia_recepcion,
        detalle_fn=lambda p: (
            f"{p.get('recepciones', 0)} rec · {round(_eficacia_recepcion(p) * 100)}%"
            f" · {p.get('recepcion_err', 0)} err"
        ),
    )

    mejor_saque = None
    with_aces = [p for p in players if p['saque_aces'] > 0]
    if with_aces:
        top = max(with_aces, key=lambda p: p['saque_aces'])
        aces = top['saque_aces']
        label = f"{aces} ace" if aces == 1 else f"{aces} aces"
        mejor_saque = _player_destacado(top, label)

    peor_saque = _peor_errores(players, 'saque_err', min_err=2)

    mejor_bloqueo = None
    with_blo = [p for p in players if (p.get('bloqueo_pts') or 0) > 0 or (p.get('bloqueo_toques') or 0) > 0]
    if with_blo:
        top = max(with_blo, key=lambda p: (p.get('bloqueo_pts') or 0, p.get('bloqueo_toques') or 0))
        if top.get('bloqueo_pts', 0) > 0:
            detalle = f"{top['bloqueo_pts']} pts"
        else:
            detalle = f"{top['bloqueo_toques']} toques"
        mejor_bloqueo = _player_destacado(top, detalle)

    mejor_defensa = _mejor_fundamento_volumen_min_eficacia(
        players,
        volumen_fn=lambda p: p.get('defensas') or 0,
        eficacia_fn=_eficacia_defensa,
        detalle_fn=lambda p: (
            f"{p.get('defensas', 0)} def · {round(_eficacia_defensa(p) * 100)}%"
            f" · {p.get('defensa_err', 0)} err"
        ),
    )

    return {
        'ataque': {'mejor': mejor_ataque, 'a_mejorar': peor_ataque},
        'recepcion': {'mejor': mejor_recepcion, 'a_mejorar': _peor_errores(players, 'recepcion_err')},
        'saque': {'mejor': mejor_saque, 'a_mejorar': peor_saque},
        'bloqueo': {'mejor': mejor_bloqueo, 'a_mejorar': _peor_errores(players, 'bloqueo_err')},
        'defensa': {'mejor': mejor_defensa, 'a_mejorar': _peor_errores(players, 'defensa_err')},
    }


def _aggregate_players_stats(detalle_sets):
    """Suma estadísticas de jugadoras a través de varios sets."""
    agg = {}
    sum_keys = [
        'balance', 'puntos', 'errores', 'ataque_kills', 'ataque_err', 'ataque_swings',
        'saque_aces', 'saque_err', 'saques', 'recepcion_pos', 'recepcion_err', 'recepciones',
        'bloqueo_pts', 'bloqueo_toques', 'bloqueo_err',
        'defensas', 'defensa_err', 'asistencias', 'colocacion_err',
    ]
    for sd in detalle_sets:
        for j in sd['jugadoras']:
            jid = j['id']
            if jid not in agg:
                agg[jid] = {
                    'id': jid,
                    'dorsal': j['dorsal'],
                    'nombre': j['nombre'],
                    'acciones': 0,
                    **{k: 0 for k in sum_keys},
                }
            row = agg[jid]
            row['acciones'] += j.get('acciones', 0) or 0
            for k in sum_keys:
                row[k] += j.get(k, 0) or 0
    players = list(agg.values())
    for p in players:
        swings = p['ataque_swings']
        p['ataque_pct'] = round((p['ataque_kills'] - p['ataque_err']) / swings, 3) if swings else None
    return players


def build_match_totals(partido, detalle_sets):
    """Box score agregado de todo el partido (solo si hay más de un set)."""
    if len(detalle_sets) <= 1:
        return None
    players = _aggregate_players_stats(detalle_sets)
    players.sort(key=lambda x: (-x['balance'], -x['puntos']))
    sets_local, sets_rival = count_sets_won(partido)
    return {
        'label': 'TOTAL PARTIDO',
        'score': f'{sets_local}–{sets_rival}',
        'score_local': sets_local,
        'score_rival': sets_rival,
        'jugadoras': players,
        'totales': _totals_row(players),
        'sideout_pct': calc_sideout_pct(partido, None),
        'k1_efi': calc_k1_complex_pct(partido, None),
        'k2_efi': calc_k2_complex_pct(partido, None),
    }


def build_set_leaders(partido, set_num):
    """Tres líderes independientes del set para el panel en banquillo."""
    report = build_set_report(partido, set_num)
    return _leaders_from_players(report['jugadoras'])


def build_destacados_por_accion(partido, set_num, min_ataques=3):
    """Cara y cruz por fundamento para el panel lateral en vivo."""
    report = build_set_report(partido, set_num)
    return _destacados_from_players(report['jugadoras'], min_ataques)


def build_match_summary(partido):
    resumen = []
    for s in sorted(_rows_by_set(partido).keys()):
        local, rival = calc_set_score(partido, s)
        resumen.append({
            'set_num': s,
            'score_local': local,
            'score_rival': rival,
            'score': f'{local}–{rival}',
            'sideout_pct': calc_sideout_pct(partido, s),
            'rival_sideout_pct': calc_rival_sideout_pct(partido, s),
            'breakpoint_pct': calc_breakpoint_pct(partido, s),
        })
    return resumen


def build_full_report(partido, set_filter='global'):
    summary = build_match_summary(partido)
    if set_filter == 'global':
        sets_nums = [r['set_num'] for r in summary]
    else:
        try:
            sets_nums = [int(set_filter)]
        except (TypeError, ValueError):
            sets_nums = [r['set_num'] for r in summary]

    detalle_sets = []
    for s in sets_nums:
        sd = build_set_report(partido, s)
        sd['zonas'] = zone_performance(partido, s)
        sd['rotacion'] = rotation_matrix(partido, s)
        sd['racha_maxima'] = calc_racha_maxima(partido, s)
        sd['run_chart'] = build_run_chart(partido, s)
        sd['k1_efi'] = calc_k1_complex_pct(partido, s)
        sd['k2_efi'] = calc_k2_complex_pct(partido, s)
        sd['lideres'] = _leaders_from_players(sd['jugadoras'])
        sd['destacados_por_accion'] = _destacados_from_players(sd['jugadoras'])
        detalle_sets.append(sd)

    detalle_total = build_match_totals(partido, detalle_sets) if set_filter == 'global' else None

    return {
        'resumen_sets': summary,
        'detalle_sets': detalle_sets,
        'detalle_total': detalle_total,
        'set_filter': set_filter,
        'destacadas': build_destacadas(detalle_sets),
    }


def build_destacadas(detalle_sets, min_ataques=3):
    """Líderes y destacados del set o partido para informes."""
    titulo = 'DESTACADAS DEL PARTIDO' if len(detalle_sets) > 1 else 'DESTACADAS DEL SET'
    players = _aggregate_players_stats(detalle_sets)
    lideres = _leaders_from_players(players)
    destacados = _destacados_from_players(players, min_ataques)
    return {
        'titulo': titulo,
        'lideres': lideres,
        'destacados_por_accion': destacados,
        # Alias planos para plantillas que usan claves antiguas
        'estrella': lideres['estrella'],
        'max_anotadora': lideres['maxima_anotadora'],
        'lider_saque': lideres['mejor_saque'],
    }


def zone_performance(partido, set_num):
    """Rendimiento de Ataque y Bloqueo por zona de pista (1-6), a partir del
    campo `zona` que el Modo Rápido guarda en cada acción. Solo cuenta
    acciones con zona conocida (colocación, líbero y Modo Avanzado no la
    llevan, y quedan fuera de este desglose).

    Devuelve una lista de dicts por zona con puntos/errores/% de acierto de
    Ataque, y lo mismo de Bloqueo cuando la zona es de red (2, 3, 4).
    """
    rows = [r for r in _rows_for(partido, set_num) if r['zona'] is not None]
    rows_by_zone = defaultdict(list)
    for r in rows:
        rows_by_zone[r['zona']].append(r)

    zonas = []
    for z in range(1, 7):
        z_rows = rows_by_zone.get(z, [])
        atq = _fund_counts(z_rows, 'ATAQUE')
        blo = _fund_counts(z_rows, 'BLOQUEO')
        atq_pct = round((atq['pp'] - atq['mm']) / atq['total'] * 100, 1) if atq['total'] else None
        blo_pct = round((blo['pp'] - blo['mm']) / blo['total'] * 100, 1) if blo['total'] else None
        zonas.append({
            'zona': z,
            'es_red': z in (2, 3, 4),
            'ataque_total': atq['total'],
            'ataque_pts': atq['pp'],
            'ataque_err': atq['mm'],
            'ataque_pct': atq_pct,
            'bloqueo_total': blo['total'],
            'bloqueo_pts': blo['pp'],
            'bloqueo_err': blo['mm'],
            'bloqueo_pct': blo_pct,
        })
    return zonas


def _lado_del_punto_row(r):
    """Determina qué lado se anotó el punto representado por esta fila, o
    `None` si es una acción intermedia sin desenlace de punto (p.ej. una
    recepción o colocación en juego, calidad '=').

    Mismo criterio que `calc_set_score`: cualquier '++' en Saque/Ataque/
    Bloqueo o un Error del Rival es punto propio; un Punto del Rival o
    cualquier '--' (error directo, sea cual sea la acción) es punto rival.
    """
    if _es_punto_local(r):
        return 'nosotros'
    if _es_punto_rival(r):
        return 'rival'
    return None


def calc_racha(partido, set_num):
    """Racha de puntos consecutivos en curso (momentum) dentro del set.

    Recorre los registros del set en orden cronológico inverso y cuenta
    cuántos puntos seguidos del mismo lado se han anotado justo antes del
    estado actual del marcador (las acciones sin desenlace de punto se
    ignoran).
    """
    rows = _rows_for(partido, set_num)
    racha = 0
    lado = None
    for r in reversed(rows):
        lado_actual = _lado_del_punto_row(r)
        if lado_actual is None:
            continue
        if lado is None:
            lado = lado_actual
            racha = 1
        elif lado_actual == lado:
            racha += 1
        else:
            break
    if racha < 2:
        return {'lado': None, 'racha': 0}
    return {'lado': lado, 'racha': racha}


def calc_racha_maxima(partido, set_num):
    """Racha más larga de puntos consecutivos del mismo lado en todo el set
    (a diferencia de `calc_racha`, que solo mira el momento actual). Útil
    para el informe post-partido: "mayor racha: 5 puntos seguidos".
    """
    rows = _rows_for(partido, set_num)
    lado_actual = None
    racha_actual = 0
    mejor_lado = None
    mejor_racha = 0
    for r in rows:
        lado = _lado_del_punto_row(r)
        if lado is None:
            continue
        if lado == lado_actual:
            racha_actual += 1
        else:
            lado_actual = lado
            racha_actual = 1
        if racha_actual > mejor_racha:
            mejor_racha = racha_actual
            mejor_lado = lado_actual
    if mejor_racha < 2:
        return {'lado': None, 'racha': 0}
    return {'lado': mejor_lado, 'racha': mejor_racha}


def build_run_chart(partido, set_num):
    """Evolución del marcador punto a punto dentro del set: diferencia de
    puntos (nosotros − rival) acumulada tras cada punto disputado, en orden
    cronológico. Sirve para dibujar un "run chart" que visualiza rachas y
    momentos clave de un vistazo.
    """
    rows = _rows_for(partido, set_num)
    diffs = []
    score_local = score_rival = 0
    for r in rows:
        lado = _lado_del_punto_row(r)
        if lado is None:
            continue
        if lado == 'nosotros':
            score_local += 1
        else:
            score_rival += 1
        diffs.append(score_local - score_rival)
    return diffs


def rotation_matrix(partido, set_num):
    rows = _rows_for(partido, set_num)
    rows_by_rotacion = defaultdict(list)
    for r in rows:
        rows_by_rotacion[r['rotacion_num']].append(r)

    matrix = []
    for r_num in range(1, 7):
        r_rows = rows_by_rotacion.get(r_num, [])
        k1 = _phase_efficiency(r_rows, ['K1', 'K2'], ['RECEPCION', 'ATAQUE', 'COLOCACION'])
        k2 = _phase_efficiency(r_rows, ['K0'], ['SAQUE', 'BLOQUEO', 'DEFENSA'])
        matrix.append({
            'rotacion': r_num,
            'k1': k1 if k1 is not None else 0,
            'k2': k2 if k2 is not None else 0,
            'acciones': len(r_rows),
        })
    return matrix
