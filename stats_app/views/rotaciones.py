import json
import traceback
from django.shortcuts import get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, Http404
from ..models import RotacionSet, Jugadora, Partido


def _partido_del_entrenador(request, partido_id):
    return get_object_or_404(Partido, pk=partido_id, equipo__entrenador=request.user)


class GetRotacionActualAPI(LoginRequiredMixin, View):
    def get(self, request, partido_id):
        partido = _partido_del_entrenador(request, partido_id)
        set_n = request.GET.get('set', 1)
        rotacion = RotacionSet.objects.filter(partido=partido, set_numero=set_n, es_inicial=False).order_by('-id').first()
        if not rotacion:
            rotacion = RotacionSet.objects.filter(partido=partido, set_numero=set_n, es_inicial=True).first()
        
        if not rotacion:
            return JsonResponse({'error': 'No hay alineación inicial'}, status=404)
        
        data = {
            'pos1': {'id': rotacion.pos1.id, 'dorsal': rotacion.pos1.dorsal} if rotacion.pos1 else None,
            'pos2': {'id': rotacion.pos2.id, 'dorsal': rotacion.pos2.dorsal} if rotacion.pos2 else None,
            'pos3': {'id': rotacion.pos3.id, 'dorsal': rotacion.pos3.dorsal} if rotacion.pos3 else None,
            'pos4': {'id': rotacion.pos4.id, 'dorsal': rotacion.pos4.dorsal} if rotacion.pos4 else None,
            'pos5': {'id': rotacion.pos5.id, 'dorsal': rotacion.pos5.dorsal} if rotacion.pos5 else None,
            'pos6': {'id': rotacion.pos6.id, 'dorsal': rotacion.pos6.dorsal} if rotacion.pos6 else None,
            'libero1': {'id': rotacion.libero1.id, 'dorsal': rotacion.libero1.dorsal} if rotacion.libero1 else None,
            'libero2': {'id': rotacion.libero2.id, 'dorsal': rotacion.libero2.dorsal} if rotacion.libero2 else None,
        }
        return JsonResponse(data)

class GuardarAlineacionInicialAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        try:
            partido = _partido_del_entrenador(request, partido_id)
            data = json.loads(request.body)
            set_n = data.get('set_numero', 1)

            # Todas las jugadoras referenciadas deben pertenecer al equipo del partido.
            posiciones_ids = [
                data.get('pos1'), data.get('pos2'), data.get('pos3'),
                data.get('pos4'), data.get('pos5'), data.get('pos6'),
                data.get('libero1'), data.get('libero2'),
            ]
            ids_validos = {v for v in posiciones_ids if v}
            if ids_validos:
                encontradas = set(
                    Jugadora.objects.filter(id__in=ids_validos, equipo=partido.equipo).values_list('id', flat=True)
                )
                if encontradas != {int(i) for i in ids_validos}:
                    return JsonResponse({'error': 'Jugadora no válida para este equipo'}, status=400)

            def update_rot(es_inicial):
                qs = RotacionSet.objects.filter(
                    partido=partido, set_numero=set_n, es_inicial=es_inicial
                )
                rot = qs.order_by('-id').first() if not es_inicial else qs.first()
                if not rot:
                    rot = RotacionSet(partido=partido, set_numero=set_n, es_inicial=es_inicial)
                
                rot.pos1_id = data.get('pos1') if data.get('pos1') else None
                rot.pos2_id = data.get('pos2') if data.get('pos2') else None
                rot.pos3_id = data.get('pos3') if data.get('pos3') else None
                rot.pos4_id = data.get('pos4') if data.get('pos4') else None
                rot.pos5_id = data.get('pos5') if data.get('pos5') else None
                rot.pos6_id = data.get('pos6') if data.get('pos6') else None
                rot.libero1_id = data.get('libero1') if data.get('libero1') else None
                rot.libero2_id = data.get('libero2') if data.get('libero2') else None
                rot.save()
                return rot

            # Durante el partido (sustituciones, edición en pista) solo se actualiza
            # la rotación actual; la alineación inicial (es_inicial=True) se conserva.
            if data.get('solo_actual'):
                update_rot(False)
            else:
                update_rot(True)
                update_rot(False)
            
            return JsonResponse({'status': 'ok'})
        except Http404:
            raise
        except Exception as e:
            error_msg = f"ERROR CRÍTICO: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return JsonResponse({'error': str(e)}, status=400)

class RotarManualAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        partido = _partido_del_entrenador(request, partido_id)
        data = json.loads(request.body)
        set_n = data.get('set_numero', 1)
        direccion = data.get('direccion', 'adelante')
        modalidad = partido.modalidad
        
        actual = RotacionSet.objects.filter(partido=partido, set_numero=set_n, es_inicial=False).order_by('-id').first()
        if not actual:
            actual = RotacionSet.objects.filter(partido=partido, set_numero=set_n, es_inicial=True).first()
        
        if not actual: return JsonResponse({'error': 'No hay rotación'}, status=404)
        
        p1, p2, p3, p4, p5, p6 = actual.pos1_id, actual.pos2_id, actual.pos3_id, actual.pos4_id, actual.pos5_id, actual.pos6_id
        
        if modalidad == 'MINIVOLEY':
            if direccion == 'horario':
                # Sentido horario en rombo: [Zona 1 -> Zona 4 -> Zona 3 -> Zona 2 -> Zona 1]
                # El jugador de Zona 4 pasa a Zona 3, el de 3 a 2, el de 2 a 1 para sacar, y el de 1 a 4.
                new_p4 = p1   # Z1 -> Z4
                new_p3 = p4   # Z4 -> Z3
                new_p2 = p3   # Z3 -> Z2
                new_p1 = p2   # Z2 -> Z1
            else:
                # Sentido antihorario (deshacer): [Zona 1 -> Zona 2 -> Zona 3 -> Zona 4 -> Zona 1]
                new_p1 = p4   # Z4 -> Z1
                new_p4 = p3   # Z3 -> Z4
                new_p3 = p2   # Z2 -> Z3
                new_p2 = p1   # Z1 -> Z2
            new_p5 = None
            new_p6 = None
        else:
            if direccion == 'horario':
                # Rotación reglamentaria FIVB (sentido horario):
                # Zona1 → Zona6 → Zona5 → Zona4 → Zona3 → Zona2 → Zona1
                # Cada jugadora ocupa la zona de número inferior:
                new_p6 = p1   # quien estaba en Z1 pasa a Z6
                new_p5 = p6   # quien estaba en Z6 pasa a Z5
                new_p4 = p5   # quien estaba en Z5 pasa a Z4
                new_p3 = p4   # quien estaba en Z4 pasa a Z3
                new_p2 = p3   # quien estaba en Z3 pasa a Z2
                new_p1 = p2   # quien estaba en Z2 pasa a Z1
            else:
                # Rotación inversa (antihoraria / deshacer):
                # Zona1 → Zona2 → Zona3 → Zona4 → Zona5 → Zona6 → Zona1
                new_p2 = p1
                new_p3 = p2
                new_p4 = p3
                new_p5 = p4
                new_p6 = p5
                new_p1 = p6

        RotacionSet.objects.create(
            partido=partido, set_numero=set_n, es_inicial=False,
            pos1_id=new_p1, pos2_id=new_p2, pos3_id=new_p3, pos4_id=new_p4, pos5_id=new_p5, pos6_id=new_p6,
            libero1_id=actual.libero1_id, libero2_id=actual.libero2_id
        )
        
        return JsonResponse({'status': 'ok'})

class ActualizarPosicionJugadoraAPI(LoginRequiredMixin, View):
    def post(self, request):
        data = json.loads(request.body)
        jugadora_id = data.get('jugadora_id')
        nueva_pos = data.get('posicion')
        
        try:
            jugadora = Jugadora.objects.get(id=jugadora_id, equipo__entrenador=request.user)
            jugadora.posicion = nueva_pos
            jugadora.save()
            return JsonResponse({'status': 'ok', 'mensaje': f'Posición de {jugadora.nombre} actualizada'})
        except Jugadora.DoesNotExist:
            return JsonResponse({'status': 'error', 'mensaje': 'Jugadora no encontrada'}, status=404)
