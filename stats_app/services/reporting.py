"""Agregación de estadísticas para informes en vivo y post-partido."""

from django.db.models import Q

from stats_app.models import Jugadora, RegistroEstadistica, RotacionSet

FUNDAMENTOS = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
FUNDAMENTOS_SCOUT = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']


def _qs_partido(partido, set_num=None):
    qs = RegistroEstadistica.objects.filter(partido=partido)
    if set_num is not None:
        qs = qs.filter(set_numero=set_num)
    return qs


def calc_set_score(partido, set_num):
    qs = _qs_partido(partido, set_num)
    local = (
        qs.filter(accion__in=['SAQUE', 'ATAQUE', 'BLOQUEO'], calidad='++').count()
        + qs.filter(accion='ERROR_RIVAL').count()
    )
    rival = qs.filter(Q(accion='PUNTO_RIVAL') | Q(calidad='--')).count()
    return local, rival


def _phase_efficiency(qs, fases, acciones=None):
    phase_qs = qs.filter(tipo_fase__in=fases)
    if acciones:
        phase_qs = phase_qs.filter(accion__in=acciones)
    wins = phase_qs.filter(
        Q(accion__in=['SAQUE', 'ATAQUE', 'BLOQUEO'], calidad='++') | Q(accion='ERROR_RIVAL')
    ).count()
    losses = phase_qs.filter(Q(accion='PUNTO_RIVAL') | Q(calidad='--')).count()
    total = wins + losses
    if total == 0:
        return None
    return round(wins / total * 100, 1)


def calc_sideout_pct(partido, set_num):
    """% de side-out cuando recibimos (fases K1/K2)."""
    qs = _qs_partido(partido, set_num)
    pct = _phase_efficiency(qs, ['K1', 'K2'])
    if pct is not None:
        return pct
    rec = qs.filter(accion='RECEPCION')
    total = rec.count()
    if total == 0:
        return None
    positivos = rec.filter(calidad__in=['++', '+']).count()
    return round(positivos / total * 100, 1)


def calc_breakpoint_pct(partido, set_num):
    """% de puntos ganados con nuestro saque (fase K0)."""
    qs = _qs_partido(partido, set_num)
    return _phase_efficiency(qs, ['K0'])


def calc_rival_sideout_pct(partido, set_num):
    """Aproximación del side-out rival cuando nosotros sacamos (K0)."""
    qs = _qs_partido(partido, set_num)
    k0 = qs.filter(tipo_fase='K0')
    rival_wins = k0.filter(Q(accion='PUNTO_RIVAL') | Q(calidad='--')).count()
    our_wins = k0.filter(
        Q(accion__in=['SAQUE', 'ATAQUE', 'BLOQUEO'], calidad='++') | Q(accion='ERROR_RIVAL')
    ).count()
    total = rival_wins + our_wins
    if total == 0:
        return None
    return round(rival_wins / total * 100, 1)


def _fund_counts(j_qs, accion):
    f = j_qs.filter(accion=accion)
    pp = f.filter(calidad='++').count()
    p = f.filter(calidad='+').count()
    eq = f.filter(calidad='=').count()
    m = f.filter(calidad='-').count()
    mm = f.filter(calidad='--').count()
    total = pp + p + eq + m + mm
    return {'pp': pp, 'p': p, 'eq': eq, 'm': m, 'mm': mm, 'total': total}


def player_box_row(jugadora, qs_set):
    j_qs = qs_set.filter(jugadora=jugadora, accion__in=FUNDAMENTOS_SCOUT)
    if not j_qs.exists():
        return None

    saque = _fund_counts(j_qs, 'SAQUE')
    rec = _fund_counts(j_qs, 'RECEPCION')
    col = _fund_counts(j_qs, 'COLOCACION')
    atq = _fund_counts(j_qs, 'ATAQUE')
    blo = _fund_counts(j_qs, 'BLOQUEO')
    defn = _fund_counts(j_qs, 'DEFENSA')

    puntos = j_qs.filter(calidad='++').count()
    errores = j_qs.filter(calidad='--').count()
    balance = puntos - errores
    acciones = j_qs.count()

    swings = atq['total']
    kills = atq['pp']
    hit_err = atq['mm']
    if swings > 0:
        hit_pct = round((kills - hit_err) / swings, 3)
    else:
        hit_pct = None

    rec_pos = rec['pp'] + rec['p']
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
        'recepcion_err': rec['mm'],
        'recepcion_pct': rec_pct,
        'saques': saque['total'],
        'saque_aces': saque['pp'],
        'saque_err': saque['mm'],
        'efi_global': round(max(0, balance / acciones * 100), 1) if acciones else 0,
    }


def _jugadoras_en_set(partido, set_num):
    qs = _qs_partido(partido, set_num)
    ids = set(qs.filter(jugadora__isnull=False).values_list('jugadora_id', flat=True))
    rotaciones = RotacionSet.objects.filter(partido=partido, set_numero=set_num)
    for rot in rotaciones:
        for field in ['pos1_id', 'pos2_id', 'pos3_id', 'pos4_id', 'pos5_id', 'pos6_id', 'libero1_id', 'libero2_id']:
            val = getattr(rot, field)
            if val:
                ids.add(val)
    return Jugadora.objects.filter(id__in=ids).order_by('dorsal')


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
    qs = _qs_partido(partido, set_num)
    local, rival = calc_set_score(partido, set_num)
    players = []
    for j in _jugadoras_en_set(partido, set_num):
        row = player_box_row(j, qs)
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
            'alerta': p['balance'] < 0 or (p['errores'] >= 2 and p['puntos'] == 0),
        }
        for p in report['jugadoras']
    ]
    return report


def build_match_summary(partido):
    sets = (
        RegistroEstadistica.objects.filter(partido=partido)
        .values_list('set_numero', flat=True)
        .distinct()
        .order_by('set_numero')
    )
    resumen = []
    for s in sets:
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
        # Desgloses adicionales, solo para el informe post-partido (no se
        # cargan en el informe rápido en vivo para no engordar el polling).
        sd['zonas'] = zone_performance(partido, s)
        sd['rotacion'] = rotation_matrix(partido, s)
        sd['racha_maxima'] = calc_racha_maxima(partido, s)
        sd['run_chart'] = build_run_chart(partido, s)
        detalle_sets.append(sd)

    return {
        'resumen_sets': summary,
        'detalle_sets': detalle_sets,
        'set_filter': set_filter,
        'destacadas': build_destacadas(detalle_sets),
    }


def build_destacadas(detalle_sets, min_ataques=3):
    """Líderes del set o partido para el bloque destacado del PDF."""
    agg = {}
    for sd in detalle_sets:
        for j in sd['jugadoras']:
            jid = j['id']
            if jid not in agg:
                agg[jid] = {
                    'dorsal': j['dorsal'],
                    'nombre': j['nombre'],
                    'puntos': 0,
                    'saque_aces': 0,
                    'ataque_swings': 0,
                    'ataque_kills': 0,
                    'ataque_err': 0,
                }
            a = agg[jid]
            a['puntos'] += j['puntos']
            a['saque_aces'] += j['saque_aces']
            a['ataque_swings'] += j['ataque_swings']
            a['ataque_kills'] += j['ataque_kills']
            a['ataque_err'] += j['ataque_err']

    players = list(agg.values())
    titulo = 'DESTACADAS DEL PARTIDO' if len(detalle_sets) > 1 else 'DESTACADAS DEL SET'

    max_anotadora = None
    if players:
        top = max(players, key=lambda p: p['puntos'])
        if top['puntos'] > 0:
            max_anotadora = {
                'dorsal': top['dorsal'],
                'nombre': top['nombre'],
                'puntos': top['puntos'],
            }

    lider_saque = None
    if players:
        top = max(players, key=lambda p: p['saque_aces'])
        if top['saque_aces'] > 0:
            lider_saque = {
                'dorsal': top['dorsal'],
                'nombre': top['nombre'],
                'aces': top['saque_aces'],
            }

    mejor_ataque = None
    candidatas = [p for p in players if p['ataque_swings'] >= min_ataques]
    if candidatas:
        for p in candidatas:
            p['_efi'] = (p['ataque_kills'] - p['ataque_err']) / p['ataque_swings']
        top = max(candidatas, key=lambda p: (p['_efi'], p['ataque_swings']))
        mejor_ataque = {
            'dorsal': top['dorsal'],
            'nombre': top['nombre'],
            'pct': round(top['_efi'] * 100),
            'intentos': top['ataque_swings'],
        }

    return {
        'titulo': titulo,
        'max_anotadora': max_anotadora,
        'lider_saque': lider_saque,
        'mejor_ataque': mejor_ataque,
    }


def zone_performance(partido, set_num):
    """Rendimiento de Ataque y Bloqueo por zona de pista (1-6), a partir del
    campo `zona` que el Modo Rápido guarda en cada acción. Solo cuenta
    acciones con zona conocida (colocación, líbero y Modo Avanzado no la
    llevan, y quedan fuera de este desglose).

    Devuelve una lista de dicts por zona con puntos/errores/% de acierto de
    Ataque, y lo mismo de Bloqueo cuando la zona es de red (2, 3, 4).
    """
    qs = _qs_partido(partido, set_num).filter(zona__isnull=False)
    zonas = []
    for z in range(1, 7):
        z_qs = qs.filter(zona=z)
        atq = _fund_counts(z_qs, 'ATAQUE')
        blo = _fund_counts(z_qs, 'BLOQUEO')
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


def _lado_del_punto(registro):
    """Determina qué lado se anotó el punto representado por este registro
    de estadística, o `None` si es una acción intermedia sin desenlace de
    punto (p.ej. una recepción o colocación en juego, calidad '=').

    Mismo criterio que `calc_set_score`: cualquier '++' en Saque/Ataque/
    Bloqueo o un Error del Rival es punto propio; un Punto del Rival o
    cualquier '--' (error directo, sea cual sea la acción) es punto rival.
    """
    if (registro.accion in ('SAQUE', 'ATAQUE', 'BLOQUEO') and registro.calidad == '++') \
            or registro.accion == 'ERROR_RIVAL':
        return 'nosotros'
    if registro.accion == 'PUNTO_RIVAL' or registro.calidad == '--':
        return 'rival'
    return None


def calc_racha(partido, set_num):
    """Racha de puntos consecutivos en curso (momentum) dentro del set.

    Recorre los registros del set en orden cronológico inverso y cuenta
    cuántos puntos seguidos del mismo lado se han anotado justo antes del
    estado actual del marcador (las acciones sin desenlace de punto se
    ignoran).
    """
    qs = _qs_partido(partido, set_num).order_by('-id').only('id', 'accion', 'calidad')
    racha = 0
    lado = None
    for r in qs:
        lado_actual = _lado_del_punto(r)
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
    qs = _qs_partido(partido, set_num).order_by('id').only('id', 'accion', 'calidad')
    lado_actual = None
    racha_actual = 0
    mejor_lado = None
    mejor_racha = 0
    for r in qs:
        lado = _lado_del_punto(r)
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
    qs = _qs_partido(partido, set_num).order_by('id').only('id', 'accion', 'calidad')
    diffs = []
    score_local = score_rival = 0
    for r in qs:
        lado = _lado_del_punto(r)
        if lado is None:
            continue
        if lado == 'nosotros':
            score_local += 1
        else:
            score_rival += 1
        diffs.append(score_local - score_rival)
    return diffs


def rotation_matrix(partido, set_num):
    qs = _qs_partido(partido, set_num)
    matrix = []
    for r in range(1, 7):
        r_qs = qs.filter(rotacion_num=r)
        k1 = _phase_efficiency(r_qs, ['K1', 'K2'], ['RECEPCION', 'ATAQUE', 'COLOCACION'])
        k2 = _phase_efficiency(r_qs, ['K0'], ['SAQUE', 'BLOQUEO', 'DEFENSA'])
        matrix.append({
            'rotacion': r,
            'k1': k1 if k1 is not None else 0,
            'k2': k2 if k2 is not None else 0,
            'acciones': r_qs.count(),
        })
    return matrix
