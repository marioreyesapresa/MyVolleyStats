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


def count_sets_won(partido):
    """Sets ganados por cada equipo según marcadores cerrados en BD."""
    all_sets = (
        RegistroEstadistica.objects.filter(partido=partido)
        .values_list('set_numero', flat=True)
        .distinct()
    )
    sets_local = sets_rival = 0
    for s in all_sets:
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
    sets_nums = sorted(
        RegistroEstadistica.objects.filter(partido=partido)
        .values_list('set_numero', flat=True)
        .distinct()
    )
    if not sets_nums:
        return 1
    for s in sets_nums:
        p_l, p_r = calc_set_score(partido, s)
        limit = partido.limite_puntos_set(s)
        if not ((p_l >= limit or p_r >= limit) and abs(p_l - p_r) >= 2):
            return s
    return max(sets_nums)


def build_partido_snapshot(partido):
    """Marcador y set activo desde BD para pintar la UI sin esperar al fetch."""
    set_activo = detect_set_activo(partido)
    p_local, p_rival = calc_set_score(partido, set_activo)
    sets_local, sets_rival = count_sets_won(partido)
    sets_con_datos = sorted(
        RegistroEstadistica.objects.filter(partido=partido)
        .values_list('set_numero', flat=True)
        .distinct()
    )
    return {
        'set_activo': set_activo,
        'puntos_local': p_local,
        'puntos_rival': p_rival,
        'sets_local': sets_local,
        'sets_rival': sets_rival,
        'sets_con_datos': sets_con_datos,
    }


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


def calc_k1_complex_pct(partido, set_num):
    """Calidad del complejo recepción+ataque: (++ − −−) / total acciones."""
    qs = _qs_partido(partido, set_num)
    acciones = ['RECEPCION', 'ATAQUE']
    pp = sum(qs.filter(accion=a, calidad='++').count() for a in acciones)
    mm = sum(qs.filter(accion=a, calidad='--').count() for a in acciones)
    total = sum(qs.filter(accion=a).count() for a in acciones)
    if total == 0:
        return 0
    return round(max(0, ((pp - mm) / total) * 100))


def calc_k2_complex_pct(partido, set_num):
    """Calidad del complejo saque+bloqueo+defensa: (++ − −−) / total acciones."""
    qs = _qs_partido(partido, set_num)
    acciones = ['SAQUE', 'BLOQUEO', 'DEFENSA']
    pp = sum(qs.filter(accion=a, calidad='++').count() for a in acciones)
    mm = sum(qs.filter(accion=a, calidad='--').count() for a in acciones)
    total = sum(qs.filter(accion=a).count() for a in acciones)
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

    mejor_recepcion = None
    with_rec = [p for p in players if p.get('recepcion_pos', 0) > 0]
    if with_rec:
        top = max(with_rec, key=lambda p: (p['recepcion_pos'], -(p.get('recepcion_err') or 0)))
        mejor_recepcion = _player_destacado(
            top, f"{top['recepcion_pos']} pos / {top.get('recepcion_err', 0)} err"
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

    mejor_defensa = None
    with_def = [p for p in players if p.get('defensas', 0) > 0]
    if with_def:
        top = max(with_def, key=lambda p: (p['defensas'], -(p.get('defensa_err') or 0)))
        mejor_defensa = _player_destacado(
            top, f"{top['defensas']} def / {top.get('defensa_err', 0)} err"
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
        'balance', 'puntos', 'ataque_kills', 'ataque_err', 'ataque_swings',
        'saque_aces', 'saque_err', 'recepcion_pos', 'recepcion_err',
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


def build_set_leaders(partido, set_num):
    """Tres líderes independientes del set para el panel en banquillo."""
    report = build_set_report(partido, set_num)
    return _leaders_from_players(report['jugadoras'])


def build_destacados_por_accion(partido, set_num, min_ataques=3):
    """Cara y cruz por fundamento para el panel lateral en vivo."""
    report = build_set_report(partido, set_num)
    return _destacados_from_players(report['jugadoras'], min_ataques)


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
        sd['zonas'] = zone_performance(partido, s)
        sd['rotacion'] = rotation_matrix(partido, s)
        sd['racha_maxima'] = calc_racha_maxima(partido, s)
        sd['run_chart'] = build_run_chart(partido, s)
        sd['k1_efi'] = calc_k1_complex_pct(partido, s)
        sd['k2_efi'] = calc_k2_complex_pct(partido, s)
        sd['lideres'] = _leaders_from_players(sd['jugadoras'])
        sd['destacados_por_accion'] = _destacados_from_players(sd['jugadoras'])
        detalle_sets.append(sd)

    return {
        'resumen_sets': summary,
        'detalle_sets': detalle_sets,
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
