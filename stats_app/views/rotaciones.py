import json
import traceback
from django.shortcuts import get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from ..models import RotacionSet, Jugadora

class GetRotacionActualAPI(LoginRequiredMixin, View):
    def get(self, request, partido_id):
        set_n = request.GET.get('set', 1)
        rotacion = RotacionSet.objects.filter(partido_id=partido_id, set_numero=set_n, es_inicial=False).order_by('-fecha_actualizacion').first()
        if not rotacion:
            rotacion = RotacionSet.objects.filter(partido_id=partido_id, set_numero=set_n, es_inicial=True).first()
        
        if not rotacion:
            return JsonResponse({'error': 'No hay alineación inicial'}, status=404)
        
        data = {
            'pos1': {'id': rotacion.pos1.id, 'dorsal': rotacion.pos1.dorsal} if rotacion.pos1 else None,
            'pos2': {'id': rotacion.pos2.id, 'dorsal': rotacion.pos2.dorsal} if rotacion.pos2 else None,
            'pos3': {'id': rotacion.pos3.id, 'dorsal': rotacion.pos3.dorsal} if rotacion.pos3 else None,
            'pos4': {'id': rotacion.pos4.id, 'dorsal': rotacion.pos4.dorsal} if rotacion.pos4 else None,
            'pos5': {'id': rotacion.pos5.id, 'dorsal': rotacion.pos5.dorsal} if rotacion.pos5 else None,
            'pos6': {'id': rotacion.pos6.id, 'dorsal': rotacion.pos6.dorsal} if rotacion.pos6 else None,
        }
        return JsonResponse(data)

class GuardarAlineacionInicialAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        try:
            data = json.loads(request.body)
            set_n = data.get('set_numero', 1)
            print(f"DEBUG: Guardando alineación para partido {partido_id}, set {set_n}")
            print(f"DEBUG: Data recibida: {data}")

            def update_rot(es_inicial):
                rot = RotacionSet.objects.filter(partido_id=partido_id, set_numero=set_n, es_inicial=es_inicial).first()
                if not rot:
                    rot = RotacionSet(partido_id=partido_id, set_numero=set_n, es_inicial=es_inicial)
                
                rot.pos1_id = data.get('pos1')
                rot.pos2_id = data.get('pos2')
                rot.pos3_id = data.get('pos3')
                rot.pos4_id = data.get('pos4')
                rot.pos5_id = data.get('pos5')
                rot.pos6_id = data.get('pos6')
                rot.save()
                return rot

            update_rot(True)
            update_rot(False)
            
            return JsonResponse({'status': 'ok'})
        except Exception as e:
            error_msg = f"ERROR CRÍTICO: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return JsonResponse({'error': str(e)}, status=400)

class RotarManualAPI(LoginRequiredMixin, View):
    def post(self, request, partido_id):
        data = json.loads(request.body)
        set_n = data.get('set_numero', 1)
        direccion = data.get('direccion', 'adelante')
        
        actual = RotacionSet.objects.filter(partido_id=partido_id, set_numero=set_n, es_inicial=False).order_by('-fecha_actualizacion').first()
        if not actual:
            actual = RotacionSet.objects.filter(partido_id=partido_id, set_numero=set_n, es_inicial=True).first()
        
        if not actual: return JsonResponse({'error': 'No hay rotación'}, status=404)
        
        p1, p2, p3, p4, p5, p6 = actual.pos1_id, actual.pos2_id, actual.pos3_id, actual.pos4_id, actual.pos5_id, actual.pos6_id
        
        if direccion == 'adelante':
            new_p1, new_p6, new_p5, new_p4, new_p3, new_p2 = p2, p1, p6, p5, p4, p3
        else:
            new_p2, new_p1, new_p6, new_p5, new_p4, new_p3 = p1, p6, p5, p4, p3, p2

        RotacionSet.objects.create(
            partido_id=partido_id, set_numero=set_n, es_inicial=False,
            pos1_id=new_p1, pos2_id=new_p2, pos3_id=new_p3, pos4_id=new_p4, pos5_id=new_p5, pos6_id=new_p6
        )
        
        return JsonResponse({'status': 'ok'})

class ActualizarPosicionJugadoraAPI(LoginRequiredMixin, View):
    def post(self, request):
        data = json.loads(request.body)
        jugadora_id = data.get('jugadora_id')
        nueva_pos = data.get('posicion')
        
        try:
            jugadora = Jugadora.objects.get(id=jugadora_id)
            jugadora.posicion = nueva_pos
            jugadora.save()
            return JsonResponse({'status': 'ok', 'mensaje': f'Posición de {jugadora.nombre} actualizada'})
        except Jugadora.DoesNotExist:
            return JsonResponse({'status': 'error', 'mensaje': 'Jugadora no encontrada'}, status=404)
