from django.shortcuts import render, get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
import json
from ..models import Partido, Jugadora, RegistroEstadistica, RotacionSet
from ..services.reporting import (
    build_quick_set_report,
    build_full_report,
    calc_set_score,
    rotation_matrix,
)

class ModoPartidoView(LoginRequiredMixin, View):
    template_name = 'stats_app/modo_partido.html'

    def get(self, request, pk):
        partido = get_object_or_404(Partido, pk=pk)
        jugadoras = Jugadora.objects.filter(equipo=partido.equipo).order_by('dorsal')
        
        acciones = [
            ('SAQUE', 'Saque'),
            ('RECEPCION', 'Recepción'),
            ('COLOCACION', 'Colocación'),
            ('ATAQUE', 'Ataque'),
            ('BLOQUEO', 'Bloqueo'),
            ('DEFENSA', 'Defensa'),
        ]
        
        historial = RegistroEstadistica.objects.filter(partido=partido, set_numero=1).order_by('-id')
        historial_data = []
        for reg in historial:
            historial_data.append({
                'id': reg.id,
                'dorsal': reg.jugadora.dorsal if reg.jugadora else 'EQ',
                'accion_texto': f"{reg.get_accion_display()} {reg.calidad if reg.calidad else ''}".strip(),
                'calidad': reg.calidad
            })

        permite_libero = partido.equipo.categoria in ['CADETE', 'JUVENIL', 'JUNIOR', 'SENIOR']
        partidos_guardados = Partido.objects.filter(equipo=partido.equipo).exclude(pk=partido.pk).order_by('-fecha')
        return render(request, self.template_name, {
            'partido': partido,
            'jugadoras': jugadoras,
            'matrix_actions': acciones,
            'historial_inicial': json.dumps(historial_data),
            'permite_libero': permite_libero,
            'partidos_guardados': partidos_guardados
        })

class RegistrarAccionAPI(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body)
            partido_id = data.get('partido_id')
            jugadora_id = data.get('jugadora_id')
            fase = data.get('fase')
            accion = data.get('accion')
            calidad = data.get('calidad', '').strip()
            set_num = data.get('set_numero', 1)
            rotacion_num = data.get('rotacion_num', 1)

            partido = get_object_or_404(Partido, pk=partido_id)
            jugadora = get_object_or_404(Jugadora, pk=jugadora_id) if jugadora_id else None

            registro = RegistroEstadistica.objects.create(
                partido=partido,
                jugadora=jugadora,
                tipo_fase=fase,
                accion=accion,
                calidad=calidad,
                set_numero=set_num,
                rotacion_num=rotacion_num
            )

            total_set = RegistroEstadistica.objects.filter(partido=partido, set_numero=set_num).count()

            return JsonResponse({
                'status': 'ok',
                'id': registro.id,
                'dorsal': jugadora.dorsal if jugadora else 'EQ',
                'accion_texto': f"{registro.get_accion_display()} {registro.calidad if registro.calidad else ''}".strip(),
                'total_acciones_set': total_set
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)

class ActualizarConfigSetAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        try:
            data = json.loads(request.body)
            partido = get_object_or_404(Partido, pk=partido_id)

            puntos_por_set = int(data.get('puntos_por_set', partido.puntos_por_set))
            puntos_set_decisivo = int(data.get('puntos_set_decisivo', partido.puntos_set_decisivo))
            sets_para_ganar = int(data.get('sets_para_ganar', partido.sets_para_ganar))

            if puntos_por_set < 1 or puntos_set_decisivo < 1 or sets_para_ganar < 1:
                return JsonResponse({'status': 'error', 'mensaje': 'Los valores deben ser mayores que 0'}, status=400)

            partido.puntos_por_set = puntos_por_set
            partido.puntos_set_decisivo = puntos_set_decisivo
            partido.sets_para_ganar = sets_para_ganar
            partido.save()

            return JsonResponse({
                'status': 'ok',
                'puntos_por_set': partido.puntos_por_set,
                'puntos_set_decisivo': partido.puntos_set_decisivo,
                'sets_para_ganar': partido.sets_para_ganar,
                'set_decisivo_numero': partido.set_decisivo_numero,
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)

class EliminarAccionAPI(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body)
            registro_id = data.get('id')
            registro = get_object_or_404(RegistroEstadistica, pk=registro_id)
            registro.delete()
            return JsonResponse({'status': 'ok', 'mensaje': 'Registro eliminado'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)

@csrf_exempt
def RegistrarCambioAPI(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        partido = get_object_or_404(Partido, id=data.get('partido_id'))
        jug_sale = get_object_or_404(Jugadora, id=data.get('sale_id'))
        jug_entra = get_object_or_404(Jugadora, id=data.get('entra_id'))
        set_num = data.get('set_numero', 1)
        rotacion_num = data.get('rotacion_num', 1)

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
    return JsonResponse({'status': 'error'}, status=400)

@csrf_exempt
def ObtenerStatsSetAPI(request):
    data = json.loads(request.body)
    partido_id = data.get('partido_id')
    set_num = data.get('set_numero', 1)
    
    stats_base = RegistroEstadistica.objects.filter(partido_id=partido_id, set_numero=set_num)
    fundamentos = ['SAQUE', 'RECEPCION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
    equipo_stats = {}
    
    for fund in fundamentos:
        qs = stats_base.filter(accion=fund)
        total = qs.count()
        if total > 0:
            pp = qs.filter(calidad='++').count()
            p = qs.filter(calidad='+').count()
            n = qs.filter(calidad='=').count()
            m = qs.filter(calidad='-').count()
            mm = qs.filter(calidad='--').count()
            
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
            equipo_stats[fund] = {'total': 0, 'pp_perc':0, 'p_perc':0, 'n_perc':0, 'm_perc':0, 'mm_perc':0, 'perfeccion': 0, 'eficacia': 0, 'errores': 0}

    stats_por_jugadora = {}
    jugadoras_en_partido = Jugadora.objects.filter(id__in=stats_base.values_list('jugadora_id', flat=True))
    
    for fund in fundamentos:
        lista_fund = []
        for j in jugadoras_en_partido:
            j_qs = stats_base.filter(jugadora=j, accion=fund)
            t = j_qs.count()
            if t > 0:
                pp = j_qs.filter(calidad='++').count()
                mm = j_qs.filter(calidad='--').count()
                efi = ((pp - mm) / t) * 100
                lista_fund.append({
                    'id': j.id, 'dorsal': j.dorsal, 'nombre': j.nombre, 
                    'total': t, 'eficiencia': round(efi, 1), 'pp': pp, 'mm': mm
                })
        lista_fund.sort(key=lambda x: x['eficiencia'], reverse=True)
        stats_por_jugadora[fund] = lista_fund[:5]

    alerta = {'dorsal': '--', 'nombre': 'TODO OK', 'mensaje': 'Sin alertas'}
    peor_efi = 100
    
    for fund in fundamentos:
        for j_stat in stats_por_jugadora[fund]:
            if j_stat['eficiencia'] < peor_efi:
                peor_efi = j_stat['eficiencia']
                if peor_efi < 0:
                    alerta = {
                        'dorsal': f"#{j_stat['dorsal']}",
                        'nombre': j_stat['nombre'],
                        'mensaje': f"{j_stat['mm']} Errores en {fund}"
                    }

    mvp = None
    if stats_por_jugadora['ATAQUE']: mvp = stats_por_jugadora['ATAQUE'][0]

    # Líderes por acción (best server, best attacker)
    mejor_saque = stats_por_jugadora['SAQUE'][0] if stats_por_jugadora.get('SAQUE') else None
    mejor_ataque = stats_por_jugadora['ATAQUE'][0] if stats_por_jugadora.get('ATAQUE') else None

    # K1: Recepción-Ataque complex (reception + attack combined efficiency)
    k1_acciones = ['RECEPCION', 'ATAQUE']
    k1_pp = sum(stats_base.filter(accion=a, calidad='++').count() for a in k1_acciones)
    k1_mm = sum(stats_base.filter(accion=a, calidad='--').count() for a in k1_acciones)
    k1_total = sum(stats_base.filter(accion=a).count() for a in k1_acciones)
    k1_efi = round(max(0, ((k1_pp - k1_mm) / k1_total) * 100)) if k1_total > 0 else 0

    # K2: Saque-Bloqueo-Defensa complex (service + block + defense combined efficiency)
    k2_acciones = ['SAQUE', 'BLOQUEO', 'DEFENSA']
    k2_pp = sum(stats_base.filter(accion=a, calidad='++').count() for a in k2_acciones)
    k2_mm = sum(stats_base.filter(accion=a, calidad='--').count() for a in k2_acciones)
    k2_total = sum(stats_base.filter(accion=a).count() for a in k2_acciones)
    k2_efi = round(max(0, ((k2_pp - k2_mm) / k2_total) * 100)) if k2_total > 0 else 0

    # Calculate score for the current set
    puntos_local = (
        stats_base.filter(accion__in=['SAQUE', 'ATAQUE', 'BLOQUEO'], calidad='++').count() +
        stats_base.filter(accion='ERROR_RIVAL').count()
    )
    puntos_rival = (
        stats_base.filter(Q(accion='PUNTO_RIVAL') | Q(calidad='--')).count()
    )

    # Calculate global sets won
    partido = get_object_or_404(Partido, id=partido_id)
    all_sets = RegistroEstadistica.objects.filter(partido=partido).values_list('set_numero', flat=True).distinct()
    sets_local = 0
    sets_rival = 0
    for s in all_sets:
        qs_s = RegistroEstadistica.objects.filter(partido=partido, set_numero=s)
        p_l = qs_s.filter(accion__in=['SAQUE', 'ATAQUE', 'BLOQUEO'], calidad='++').count() + qs_s.filter(accion='ERROR_RIVAL').count()
        p_r = qs_s.filter(Q(accion='PUNTO_RIVAL') | Q(calidad='--')).count()
        
        limit = partido.limite_puntos_set(s)
        if (p_l >= limit or p_r >= limit) and abs(p_l - p_r) >= 2:
            if p_l > p_r:
                sets_local += 1
            else:
                sets_rival += 1

    return JsonResponse({
        'status': 'ok',
        'partido_finalizado': partido.finalizado,
        'equipo': equipo_stats,
        'desglose_jugadoras': stats_por_jugadora,
        'mvp': mvp,
        'alerta': alerta,
        'puntos_local': puntos_local,
        'puntos_rival': puntos_rival,
        'sets_local': sets_local,
        'sets_rival': sets_rival,
        'k1_efi': k1_efi,
        'k2_efi': k2_efi,
        'mejor_saque': mejor_saque,
        'mejor_ataque': mejor_ataque,
        'puntos_por_set': partido.puntos_por_set,
        'puntos_set_decisivo': partido.puntos_set_decisivo,
        'sets_para_ganar': partido.sets_para_ganar,
        'set_decisivo_numero': partido.set_decisivo_numero,
        'informe_rapido': build_quick_set_report(partido, set_num),
        'rotaciones': rotation_matrix(partido, set_num),
    })

def get_stats_json(request, partido_id, set_n):
    fundamentos = ['SAQUE', 'RECEPCION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
    stats = {}
    
    qs_set = RegistroEstadistica.objects.filter(partido_id=partido_id, set_numero=set_n).select_related('jugadora')
    jug_stats = {}
    for r in qs_set:
        if not r.jugadora: continue
        jid = r.jugadora.id
        fund = r.accion
        if fund not in fundamentos: continue
        
        if jid not in jug_stats:
            jug_stats[jid] = {'nombre': r.jugadora.nombre, 'dorsal': r.jugadora.dorsal, 'funds': {}}
        if fund not in jug_stats[jid]['funds']:
            jug_stats[jid]['funds'][fund] = {'pp': 0, 'p': 0, 'mm': 0, 'total': 0}
            
        jug_stats[jid]['funds'][fund]['total'] += 1
        if r.calidad == '++': jug_stats[jid]['funds'][fund]['pp'] += 1
        elif r.calidad == '+': jug_stats[jid]['funds'][fund]['p'] += 1
        elif r.calidad == '--': jug_stats[jid]['funds'][fund]['mm'] += 1

    for fund in fundamentos:
        qs = RegistroEstadistica.objects.filter(partido_id=partido_id, set_numero=set_n, accion=fund)
        total = qs.count()
        if total > 0:
            pp = qs.filter(calidad='++').count()
            p = qs.filter(calidad='+').count()
            n = qs.filter(calidad='=').count()
            m = qs.filter(calidad='-').count()
            mm = qs.filter(calidad='--').count()
            
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
    template_name = 'stats_app/post_match_report.html'

    def get(self, request, pk):
        partido = get_object_or_404(Partido, pk=pk)
        set_filtro = request.GET.get('set', 'global')
        reporte = build_full_report(partido, set_filtro)

        sets_disponibles = (
            RegistroEstadistica.objects.filter(partido=partido)
            .values_list('set_numero', flat=True)
            .distinct()
            .order_by('set_numero')
        )

        # Gráficos (origen global o del set filtrado)
        stats_base = RegistroEstadistica.objects.filter(partido=partido)
        if set_filtro != 'global':
            stats_base = stats_base.filter(set_numero=set_filtro)

        labels_sets, p_merito, p_err_rival, p_rival = [], [], [], []
        for s in sets_disponibles:
            labels_sets.append(f"Set {s}")
            local, rival = calc_set_score(partido, s)
            qs_s = RegistroEstadistica.objects.filter(partido=partido, set_numero=s)
            p_merito.append(qs_s.filter(accion__in=['SAQUE', 'ATAQUE', 'BLOQUEO'], calidad='++').count())
            p_err_rival.append(qs_s.filter(accion='ERROR_RIVAL').count())
            p_rival.append(rival)

        origen_puntos = {
            'Ataque': stats_base.filter(accion='ATAQUE', calidad='++').count(),
            'Saque': stats_base.filter(accion='SAQUE', calidad='++').count(),
            'Bloqueo': stats_base.filter(accion='BLOQUEO', calidad='++').count(),
            'Error Rival': stats_base.filter(accion='ERROR_RIVAL').count(),
        }

        context = {
            'partido': partido,
            'set_actual': set_filtro,
            'sets_disponibles': sets_disponibles,
            'resumen_sets': reporte['resumen_sets'],
            'detalle_sets': reporte['detalle_sets'],
            'labels_sets': json.dumps(labels_sets),
            'puntos_merito': json.dumps(p_merito),
            'puntos_err_rival': json.dumps(p_err_rival),
            'puntos_rival': json.dumps(p_rival),
            'origen_labels': json.dumps(list(origen_puntos.keys())),
            'origen_data': json.dumps(list(origen_puntos.values())),
        }
        return render(request, self.template_name, context)

class FinalizarPartidoAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        try:
            partido = get_object_or_404(Partido, id=partido_id)
            partido.finalizado = True
            partido.save()
            return JsonResponse({'status': 'success', 'mensaje': 'Partido finalizado correctamente'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)}, status=500)
