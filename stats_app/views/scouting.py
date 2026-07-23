from django.shortcuts import render, get_object_or_404
from django.views.generic import View
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, Http404
from django.db import OperationalError, InterfaceError
from django.db.models import Q
from django.views.decorators.http import require_POST
import json
import logging
from ..models import Partido, Jugadora, RegistroEstadistica, RotacionSet
from ..forms import (
    RegistrarAccionForm,
    RegistrarCambioForm,
    EliminarAccionForm,
    ObtenerStatsSetForm,
    ConfigSetForm,
    MAX_ROTACION_VOLEY,
    MAX_ROTACION_MINIVOLEY,
)
from ..db_utils import reintentar_en_error_transitorio
from ..security import log_intento_acceso_no_autorizado, ocultar_detalle_interno
from ..services.reporting import (
    build_quick_set_report,
    build_full_report,
    build_quick_report,
    build_advanced_report,
    build_partido_snapshot,
    calc_k1_complex_pct,
    calc_k2_complex_pct,
    calc_set_score,
    calc_racha,
    count_sets_won,
    rotation_matrix,
    zone_performance,
    get_sets_con_datos,
    merito_y_error_rival,
    origen_puntos_totales,
    _rows_for,
    _fund_counts,
    _leaders_from_players,
    _destacados_from_players,
)

logger = logging.getLogger('stats_app.security')


def _accion_texto(accion, calidad, display):
    """Texto legible para el historial de acciones. RED no tiene escala de
    calidad (siempre es punto directo para el rival), así que se muestra sin
    el sufijo de calidad, con un icono que la distingue de un fundamento."""
    if accion == 'RED':
        return '🥅 Red (punto rival)'
    return f"{display} {calidad if calidad else ''}".strip()


def _partido_del_entrenador(request, partido_id):
    """Devuelve el partido solo si pertenece a un equipo del usuario autenticado.

    Lanza Http404 en caso contrario, de forma indistinguible de "no existe":
    así no se filtra a un entrenador si un ID de partido de otro es válido.
    El intento queda registrado en el log de seguridad para monitorización.
    """
    try:
        return get_object_or_404(Partido, pk=partido_id, equipo__entrenador=request.user)
    except Http404:
        log_intento_acceso_no_autorizado(request, 'Partido', partido_id)
        raise


def _jugadora_del_equipo(request, jugadora_id, equipo):
    """Devuelve la jugadora solo si pertenece al equipo del partido en curso.

    Igual que `_partido_del_entrenador`: un 404 aquí puede ser un ID
    inexistente o, más grave, un intento de referenciar la jugadora de otro
    equipo/entrenador en un payload de scouting (IDOR). Se audita en ambos
    casos sin revelar cuál de los dos ocurrió al llamante.
    """
    try:
        return get_object_or_404(Jugadora, pk=jugadora_id, equipo=equipo)
    except Http404:
        log_intento_acceso_no_autorizado(request, 'Jugadora', jugadora_id)
        raise


def _parsear_json(request):
    """Decodifica el body JSON de forma segura.

    Devuelve (data, error_response). Si `error_response` no es None, la
    vista debe devolverlo inmediatamente: evita que un body corrupto o no
    JSON (payload malicioso, binario, vacío) propague una excepción no
    controlada hasta un 500.
    """
    try:
        data = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None, JsonResponse({'status': 'error', 'mensaje': 'JSON inválido'}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({'status': 'error', 'mensaje': 'Se esperaba un objeto JSON'}, status=400)
    return data, None


def _form_invalido(form):
    return JsonResponse(
        {'status': 'error', 'mensaje': 'Datos de entrada inválidos', 'errores': form.errors},
        status=400,
    )


def _validar_rango_zona_modalidad(partido, valor, campo='rotacion_num'):
    """Verifica que un número de zona/rotación (1-6) respete el límite real
    de la modalidad del partido (1-6 en VOLEY, 1-4 en MINIVOLEY).

    Compartida entre `rotacion_num` y `zona`: ambos usan el mismo rango de
    zonas de pista. Devuelve una JsonResponse de error si el valor está
    fuera de rango, o `None` si es válido (o si `valor` es `None`, ya que
    ambos campos son opcionales). El formulario ya acota el valor a [1, 6]
    (el máximo universal); aquí se aplica el límite más estricto de
    MINIVOLEY, que solo se conoce tras resolver el partido.
    """
    if valor is None:
        return None
    max_rotacion = MAX_ROTACION_MINIVOLEY if partido.modalidad == 'MINIVOLEY' else MAX_ROTACION_VOLEY
    if valor > max_rotacion:
        return JsonResponse(
            {
                'status': 'error',
                'mensaje': f'{campo}={valor} fuera de rango para modalidad '
                           f'{partido.modalidad} (máx. {max_rotacion}).',
            },
            status=400,
        )
    return None


class ModoPartidoView(LoginRequiredMixin, View):
    template_name = 'stats_app/modo_partido.html'

    def get(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        jugadoras = Jugadora.objects.filter(equipo=partido.equipo).order_by('dorsal')

        acciones = [
            ('SAQUE', 'Saque'),
            ('RECEPCION', 'Recepción'),
            ('COLOCACION', 'Colocación'),
            ('ATAQUE', 'Ataque'),
            ('BLOQUEO', 'Bloqueo'),
            ('DEFENSA', 'Defensa'),
        ]

        historial = RegistroEstadistica.objects.filter(
            partido=partido, set_numero=1
        ).select_related('jugadora').order_by('-id')
        historial_data = []
        for reg in historial:
            historial_data.append({
                'id': reg.id,
                'dorsal': reg.jugadora.dorsal if reg.jugadora else 'EQ',
                'accion_texto': _accion_texto(reg.accion, reg.calidad, reg.get_accion_display()),
                'calidad': reg.calidad
            })

        permite_libero = partido.equipo.categoria in ['CADETE', 'JUVENIL', 'JUNIOR', 'SENIOR']
        partidos_guardados = Partido.objects.filter(equipo=partido.equipo).exclude(pk=partido.pk).order_by('-fecha')
        marcador_inicial = build_partido_snapshot(partido)
        return render(request, self.template_name, {
            'partido': partido,
            'jugadoras': jugadoras,
            'matrix_actions': acciones,
            'historial_inicial': json.dumps(historial_data),
            'marcador_inicial': marcador_inicial,
            'marcador_inicial_json': json.dumps(marcador_inicial),
            'permite_libero': permite_libero,
            'partidos_guardados': partidos_guardados
        })

class RegistrarAccionAPI(LoginRequiredMixin, View):
    @reintentar_en_error_transitorio()
    def post(self, request):
        data, error = _parsear_json(request)
        if error:
            return error

        form = RegistrarAccionForm(data)
        if not form.is_valid():
            return _form_invalido(form)
        cd = form.cleaned_data

        try:
            partido = _partido_del_entrenador(request, cd['partido_id'])

            rotacion_num = cd.get('rotacion_num') or 1
            error_rango = _validar_rango_zona_modalidad(partido, rotacion_num, 'rotacion_num')
            if error_rango:
                return error_rango

            zona = cd.get('zona')
            error_zona = _validar_rango_zona_modalidad(partido, zona, 'zona')
            if error_zona:
                return error_zona

            jugadora_id = cd.get('jugadora_id')
            jugadora = _jugadora_del_equipo(request, jugadora_id, partido.equipo) if jugadora_id else None

            registro = RegistroEstadistica.objects.create(
                partido=partido,
                jugadora=jugadora,
                tipo_fase=cd.get('fase') or '',
                accion=cd['accion'],
                calidad=cd.get('calidad') or '',
                set_numero=cd.get('set_numero') or 1,
                rotacion_num=rotacion_num,
                zona=zona,
            )

            total_set = RegistroEstadistica.objects.filter(partido=partido, set_numero=registro.set_numero).count()

            return JsonResponse({
                'status': 'ok',
                'id': registro.id,
                'dorsal': jugadora.dorsal if jugadora else 'EQ',
                'accion_texto': _accion_texto(registro.accion, registro.calidad, registro.get_accion_display()),
                'total_acciones_set': total_set
            })
        except Http404:
            raise
        except (OperationalError, InterfaceError):
            # Error transitorio de conexión: se deja propagar para que
            # @reintentar_en_error_transitorio lo capture y reintente en
            # vez de convertirlo aquí en un 400 definitivo.
            raise
        except Exception as e:
            logger.exception('Error inesperado en RegistrarAccionAPI')
            return JsonResponse({'status': 'error', 'mensaje': ocultar_detalle_interno(e)}, status=400)

class ActualizarConfigSetAPI(LoginRequiredMixin, View):
    @reintentar_en_error_transitorio()
    def post(self, request, partido_id):
        try:
            partido = _partido_del_entrenador(request, partido_id)
            data, error = _parsear_json(request)
            if error:
                return error

            form = ConfigSetForm(data)
            if not form.is_valid():
                return _form_invalido(form)
            cd = form.cleaned_data

            partido.puntos_por_set = cd['puntos_por_set']
            partido.puntos_set_decisivo = cd['puntos_set_decisivo']
            partido.sets_para_ganar = cd['sets_para_ganar']
            partido.save()

            return JsonResponse({
                'status': 'ok',
                'puntos_por_set': partido.puntos_por_set,
                'puntos_set_decisivo': partido.puntos_set_decisivo,
                'sets_para_ganar': partido.sets_para_ganar,
                'set_decisivo_numero': partido.set_decisivo_numero,
            })
        except Http404:
            raise
        except (OperationalError, InterfaceError):
            # Error transitorio de conexión: se deja propagar para que
            # @reintentar_en_error_transitorio lo capture y reintente en
            # vez de convertirlo aquí en un 400 definitivo.
            raise
        except Exception as e:
            logger.exception('Error inesperado en ActualizarConfigSetAPI')
            return JsonResponse({'status': 'error', 'mensaje': ocultar_detalle_interno(e)}, status=400)

class EliminarAccionAPI(LoginRequiredMixin, View):
    @reintentar_en_error_transitorio()
    def post(self, request):
        try:
            data, error = _parsear_json(request)
            if error:
                return error

            form = EliminarAccionForm(data)
            if not form.is_valid():
                return _form_invalido(form)

            registro_id = form.cleaned_data['id']
            try:
                registro = get_object_or_404(
                    RegistroEstadistica, pk=registro_id, partido__equipo__entrenador=request.user
                )
            except Http404:
                log_intento_acceso_no_autorizado(request, 'RegistroEstadistica', registro_id)
                raise
            registro.delete()
            return JsonResponse({'status': 'ok', 'mensaje': 'Registro eliminado'})
        except Http404:
            raise
        except (OperationalError, InterfaceError):
            # Error transitorio de conexión: se deja propagar para que
            # @reintentar_en_error_transitorio lo capture y reintente en
            # vez de convertirlo aquí en un 400 definitivo.
            raise
        except Exception as e:
            logger.exception('Error inesperado en EliminarAccionAPI')
            return JsonResponse({'status': 'error', 'mensaje': ocultar_detalle_interno(e)}, status=400)


@login_required
@require_POST
@reintentar_en_error_transitorio()
def RegistrarCambioAPI(request):
    data, error = _parsear_json(request)
    if error:
        return error

    form = RegistrarCambioForm(data)
    if not form.is_valid():
        return _form_invalido(form)
    cd = form.cleaned_data

    partido = _partido_del_entrenador(request, cd['partido_id'])

    rotacion_num = cd.get('rotacion_num') or 1
    error_rango = _validar_rango_zona_modalidad(partido, rotacion_num, 'rotacion_num')
    if error_rango:
        return error_rango

    jug_sale = _jugadora_del_equipo(request, cd['sale_id'], partido.equipo)
    jug_entra = _jugadora_del_equipo(request, cd['entra_id'], partido.equipo)
    set_num = cd.get('set_numero') or 1

    registro = RegistroEstadistica.objects.create(
        partido=partido,
        jugadora=jug_entra,
        accion='SUSTITUCION',
        calidad='=',
        set_numero=set_num,
        tipo_fase='K1',
        rotacion_num=rotacion_num
    )

    return JsonResponse({
        'status': 'ok',
        'id': registro.id,
        'accion_texto': f"🔄 CAMBIO: #{jug_sale.dorsal} > #{jug_entra.dorsal}",
        'dorsal': jug_entra.dorsal
    })


@login_required
@require_POST
@reintentar_en_error_transitorio()
def ObtenerStatsSetAPI(request):
    data, error = _parsear_json(request)
    if error:
        return error

    form = ObtenerStatsSetForm(data)
    if not form.is_valid():
        return _form_invalido(form)
    cd = form.cleaned_data

    partido = _partido_del_entrenador(request, cd['partido_id'])
    partido_id = partido.id
    set_num = cd.get('set_numero') or 1
    ligero = cd.get('ligero') or False

    equipo_stats = {}
    stats_por_jugadora = {}
    if not ligero:
        rows_set = _rows_for(partido, set_num)
        fundamentos = ['SAQUE', 'RECEPCION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']

        for fund in fundamentos:
            c = _fund_counts(rows_set, fund)
            total = c['total']
            if total > 0:
                pp, p, n, m, mm = c['pp'], c['p'], c['eq'], c['m'], c['mm']
                perfeccion = ((pp + p) / total) * 100
                eficacia = ((pp - mm) / total) * 100

                equipo_stats[fund] = {
                    'total': total,
                    'pp_perc': (pp / total) * 100,
                    'p_perc': (p / total) * 100,
                    'n_perc': (n / total) * 100,
                    'm_perc': (m / total) * 100,
                    'mm_perc': (mm / total) * 100,
                    'perfeccion': round(perfeccion, 1),
                    'eficacia': round(eficacia, 1),
                    'errores': mm
                }
            else:
                equipo_stats[fund] = {
                    'total': 0, 'pp_perc': 0, 'p_perc': 0, 'n_perc': 0, 'm_perc': 0,
                    'mm_perc': 0, 'perfeccion': 0, 'eficacia': 0, 'errores': 0,
                }

        rows_por_jugadora = {}
        for r in rows_set:
            jid = r['jugadora_id']
            if jid is None:
                continue
            rows_por_jugadora.setdefault(jid, []).append(r)

        jugadoras_en_partido = Jugadora.objects.filter(id__in=rows_por_jugadora.keys())
        jugadoras_por_id = {j.id: j for j in jugadoras_en_partido}
        for fund in fundamentos:
            lista_fund = []
            for jid, j_rows in rows_por_jugadora.items():
                c = _fund_counts(j_rows, fund)
                t = c['total']
                if t > 0:
                    pp, mm = c['pp'], c['mm']
                    efi = ((pp - mm) / t) * 100
                    j = jugadoras_por_id[jid]
                    lista_fund.append({
                        'id': j.id, 'dorsal': j.dorsal, 'nombre': j.nombre,
                        'total': t, 'eficiencia': round(efi, 1), 'pp': pp, 'mm': mm
                    })
            lista_fund.sort(key=lambda x: x['eficiencia'], reverse=True)
            stats_por_jugadora[fund] = lista_fund[:5]

    informe_rapido = build_quick_set_report(partido, set_num)
    lideres = _leaders_from_players(informe_rapido['jugadoras'])
    destacados_por_accion = _destacados_from_players(informe_rapido['jugadoras'])

    k1_efi = calc_k1_complex_pct(partido, set_num)
    k2_efi = calc_k2_complex_pct(partido, set_num)

    puntos_local = informe_rapido['score_local']
    puntos_rival = informe_rapido['score_rival']

    sets_local, sets_rival = count_sets_won(partido)
    sets_con_datos = get_sets_con_datos(partido)

    payload = {
        'status': 'ok',
        'partido_finalizado': partido.finalizado,
        'equipo': equipo_stats,
        'desglose_jugadoras': stats_por_jugadora,
        'lideres': lideres,
        'destacados_por_accion': destacados_por_accion,
        'puntos_local': puntos_local,
        'puntos_rival': puntos_rival,
        'sets_local': sets_local,
        'sets_rival': sets_rival,
        'k1_efi': k1_efi,
        'k2_efi': k2_efi,
        'puntos_por_set': partido.puntos_por_set,
        'puntos_set_decisivo': partido.puntos_set_decisivo,
        'sets_para_ganar': partido.sets_para_ganar,
        'set_decisivo_numero': partido.set_decisivo_numero,
        'informe_rapido': informe_rapido,
        'racha': calc_racha(partido, set_num),
        'sets_con_datos': sets_con_datos,
        'ligero': ligero,
    }
    if not ligero:
        payload['rotaciones'] = rotation_matrix(partido, set_num)
        payload['zonas'] = zone_performance(partido, set_num)
    else:
        payload['rotaciones'] = []
        payload['zonas'] = []
    return JsonResponse(payload)


@login_required
def get_stats_json(request, partido_id, set_n):
    partido = _partido_del_entrenador(request, partido_id)
    fundamentos = ['SAQUE', 'RECEPCION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
    stats = {}
    
    qs_set = RegistroEstadistica.objects.filter(partido=partido, set_numero=set_n).select_related('jugadora')
    jug_stats = {}
    equipo_counts = {f: {'pp': 0, 'p': 0, 'eq': 0, 'm': 0, 'mm': 0, 'total': 0} for f in fundamentos}
    for r in qs_set:
        fund = r.accion
        if fund not in fundamentos:
            continue

        ec = equipo_counts[fund]
        ec['total'] += 1
        if r.calidad == '++': ec['pp'] += 1
        elif r.calidad == '+': ec['p'] += 1
        elif r.calidad == '=': ec['eq'] += 1
        elif r.calidad == '-': ec['m'] += 1
        elif r.calidad == '--': ec['mm'] += 1

        if not r.jugadora: continue
        jid = r.jugadora.id

        if jid not in jug_stats:
            jug_stats[jid] = {'nombre': r.jugadora.nombre, 'dorsal': r.jugadora.dorsal, 'funds': {}}
        if fund not in jug_stats[jid]['funds']:
            jug_stats[jid]['funds'][fund] = {'pp': 0, 'p': 0, 'mm': 0, 'total': 0}
            
        jug_stats[jid]['funds'][fund]['total'] += 1
        if r.calidad == '++': jug_stats[jid]['funds'][fund]['pp'] += 1
        elif r.calidad == '+': jug_stats[jid]['funds'][fund]['p'] += 1
        elif r.calidad == '--': jug_stats[jid]['funds'][fund]['mm'] += 1

    for fund in fundamentos:
        ec = equipo_counts[fund]
        total = ec['total']
        if total > 0:
            pp, p, n, m, mm = ec['pp'], ec['p'], ec['eq'], ec['m'], ec['mm']
            eficacia = ((pp + p) - mm) / total * 100
            
            stats[fund] = {
                'total': total,
                'positivos': pp + p,
                'neutros': n,
                'errores': mm,
                'green_perc': ((pp + p) / total) * 100,
                'gray_perc': (n / total) * 100,
                'orange_perc': (m / total) * 100,
                'red_perc': (mm / total) * 100,
                'eficacia': round(eficacia, 1),
                'mejor': None
            }
            
            mejor = None
            peor = None
            mejor_efi = -101
            peor_efi = 101
            
            for jid, data in jug_stats.items():
                if fund in data['funds'] and data['funds'][fund]['total'] >= 1:
                    f_data = data['funds'][fund]
                    efi = ((f_data['pp'] + f_data['p']) - f_data['mm']) / f_data['total'] * 100
                    
                    if efi > mejor_efi:
                        mejor_efi = efi
                        mejor = {'dorsal': data['dorsal'], 'nombre': data['nombre'], 'efi': round(efi, 1)}
                    
                    if efi < peor_efi:
                        peor_efi = efi
                        peor = {'dorsal': data['dorsal'], 'nombre': data['nombre'], 'efi': round(efi, 1)}
            
            stats[fund]['mejor'] = mejor
            stats[fund]['peor'] = peor if (peor != mejor or len(jug_stats) == 1) else None
        else:
            stats[fund] = {
                'total': 0, 'positivos': 0, 'neutros': 0, 'errores': 0,
                'green_perc': 0, 'gray_perc': 0, 'orange_perc': 0, 'red_perc': 0, 'eficacia': 0,
                'mejor': None, 'peor': None
            }
            
    seguimiento = []
    for jid, data in jug_stats.items():
        t = sum(f['total'] for f in data['funds'].values())
        if t > 0:
            pp = sum(f['pp'] for f in data['funds'].values())
            p = sum(f['p'] for f in data['funds'].values())
            mm = sum(f['mm'] for f in data['funds'].values())
            efi_global = ((pp + p) - mm) / t * 100
            
            peor_fund = None
            p_f_efi = 101
            for f, fd in data['funds'].items():
                f_efi = ((fd['pp'] + fd['p']) - fd['mm']) / fd['total'] * 100
                if f_efi < p_f_efi:
                    p_f_efi = f_efi
                    peor_fund = f
            
            seguimiento.append({
                'jugadora_id': str(jid),
                'dorsal': data['dorsal'],
                'nombre': data['nombre'],
                'eficacia': round(efi_global, 1),
                'fundamento': peor_fund
            })
    
    seguimiento.sort(key=lambda x: x['eficacia'])
    alertas_cambio = seguimiento[:3]
            
    return JsonResponse({'status': 'ok', 'stats': stats, 'alertas_cambio': alertas_cambio})

class PartidoStatsFinalView(LoginRequiredMixin, View):
    """Informe web de estadísticas rápidas (zonas, saldo binario, destacados)."""
    template_name = 'stats_app/post_match_report.html'

    def get(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        set_filtro = request.GET.get('set', 'global')
        reporte = build_quick_report(partido, set_filtro)

        sets_disponibles = get_sets_con_datos(partido)
        set_num_grafico = None if set_filtro == 'global' else set_filtro

        labels_sets, p_merito, p_err_rival, p_rival = [], [], [], []
        for s in sets_disponibles:
            labels_sets.append(f"Set {s}")
            local, rival = calc_set_score(partido, s)
            merito, err_rival = merito_y_error_rival(partido, s)
            p_merito.append(merito)
            p_err_rival.append(err_rival)
            p_rival.append(rival)

        origen_puntos = origen_puntos_totales(partido, set_num_grafico)

        context = {
            'partido': partido,
            'set_actual': set_filtro,
            'sets_disponibles': sets_disponibles,
            'resumen_sets': reporte['resumen_sets'],
            'detalle_sets': reporte['detalle_sets'],
            'detalle_total': reporte.get('detalle_total'),
            'resumen_totales': reporte.get('resumen_totales'),
            'labels_sets': json.dumps(labels_sets),
            'puntos_merito': json.dumps(p_merito),
            'puntos_err_rival': json.dumps(p_err_rival),
            'puntos_rival': json.dumps(p_rival),
            'origen_labels': json.dumps(list(origen_puntos.keys())),
            'origen_data': json.dumps(list(origen_puntos.values())),
            'destacadas': reporte['destacadas'],
            'tipo_informe': 'rapido',
        }
        return render(request, self.template_name, context)


class PartidoStatsAvanzadoView(LoginRequiredMixin, View):
    """Informe web técnico con escala completa de calidad (++/+ /=/ -/--)."""
    template_name = 'stats_app/post_match_report_avanzado.html'

    def get(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        set_filtro = request.GET.get('set', 'global')
        reporte = build_advanced_report(partido, set_filtro)
        sets_disponibles = get_sets_con_datos(partido)

        context = {
            'partido': partido,
            'set_actual': set_filtro,
            'sets_disponibles': sets_disponibles,
            'reporte': reporte,
            'resumen_sets': reporte['resumen_sets'],
            'detalle_sets': reporte['detalle_sets'],
            'detalle_total': reporte.get('detalle_total'),
            'resumen_totales': reporte.get('resumen_totales'),
            'fundamentos_orden': reporte['fundamentos_orden'],
            'fundamento_labels': reporte['fundamento_labels'],
            'fundamentos_meta': reporte['fundamentos_meta'],
            'calidad_labels': reporte['calidad_labels'],
            'tipo_informe': 'avanzado',
        }
        return render(request, self.template_name, context)

class FinalizarPartidoAPI(LoginRequiredMixin, View):
    @reintentar_en_error_transitorio()
    def post(self, request, partido_id):
        try:
            partido = _partido_del_entrenador(request, partido_id)
            partido.finalizado = True
            partido.save()
            return JsonResponse({'status': 'success', 'mensaje': 'Partido finalizado correctamente'})
        except Http404:
            raise
        except (OperationalError, InterfaceError):
            # Error transitorio de conexión: se deja propagar para que
            # @reintentar_en_error_transitorio lo capture y reintente en
            # vez de convertirlo aquí en un 400 definitivo.
            raise
        except Exception as e:
            logger.exception('Error inesperado en FinalizarPartidoAPI')
            return JsonResponse({'status': 'error', 'error': ocultar_detalle_interno(e)}, status=500)


class ReabrirPartidoAPI(LoginRequiredMixin, View):
    """Permite corregir un cierre accidental: vuelve a dejar el partido
    editable en scout. Las estadísticas no se tocan; solo se quita el
    flag `finalizado` y se invalida la caché de PDFs."""

    @reintentar_en_error_transitorio()
    def post(self, request, partido_id):
        try:
            partido = _partido_del_entrenador(request, partido_id)
            if not partido.finalizado:
                return JsonResponse({'status': 'success', 'mensaje': 'El partido ya estaba abierto'})
            partido.finalizado = False
            partido.save(update_fields=['finalizado'])
            from ..services.informes_cache import invalidar_cache_informes_partido
            invalidar_cache_informes_partido(partido.pk)
            return JsonResponse({'status': 'success', 'mensaje': 'Partido reabierto correctamente'})
        except Http404:
            raise
        except (OperationalError, InterfaceError):
            raise
        except Exception as e:
            logger.exception('Error inesperado en ReabrirPartidoAPI')
            return JsonResponse({'status': 'error', 'error': ocultar_detalle_interno(e)}, status=500)
