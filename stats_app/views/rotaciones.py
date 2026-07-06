import json
import logging
from django.shortcuts import get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, Http404
from django.db import OperationalError, InterfaceError
from ..models import RotacionSet, Jugadora, Partido
from ..forms import AlineacionInicialForm, RotarManualForm, ActualizarPosicionForm
from ..db_utils import reintentar_en_error_transitorio
from ..security import log_intento_acceso_no_autorizado, ocultar_detalle_interno

logger = logging.getLogger('stats_app.security')


def _partido_del_entrenador(request, partido_id):
    """Devuelve el partido solo si pertenece al entrenador autenticado.

    Un 404 aquí queda auditado: puede ser un ID inexistente o un intento de
    acceder/modificar la rotación de un partido de otro entrenador (IDOR).
    """
    try:
        return get_object_or_404(Partido, pk=partido_id, equipo__entrenador=request.user)
    except Http404:
        log_intento_acceso_no_autorizado(request, 'Partido', partido_id)
        raise


def _parsear_json(request):
    try:
        data = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None, JsonResponse({'error': 'JSON inválido'}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({'error': 'Se esperaba un objeto JSON'}, status=400)
    return data, None


def _form_invalido(form):
    return JsonResponse({'error': 'Datos de entrada inválidos', 'errores': form.errors}, status=400)


class GetRotacionActualAPI(LoginRequiredMixin, View):
    @reintentar_en_error_transitorio()
    def get(self, request, partido_id):
        partido = _partido_del_entrenador(request, partido_id)
        try:
            set_n = int(request.GET.get('set', 1))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Parámetro "set" inválido'}, status=400)
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
    @reintentar_en_error_transitorio()
    def post(self, request, partido_id):
        try:
            partido = _partido_del_entrenador(request, partido_id)
            data, error = _parsear_json(request)
            if error:
                return error

            # Los campos de posición llegan a veces como '' (zona vacía) desde
            # el frontend; se normalizan a None antes de validar como enteros.
            data = {k: (None if v == '' else v) for k, v in data.items()}
            form = AlineacionInicialForm(data)
            if not form.is_valid():
                return _form_invalido(form)
            cd = form.cleaned_data
            set_n = cd.get('set_numero') or 1

            posiciones_ids = [
                cd.get('pos1'), cd.get('pos2'), cd.get('pos3'),
                cd.get('pos4'), cd.get('pos5'), cd.get('pos6'),
                cd.get('libero1'), cd.get('libero2'),
            ]
            ids_validos = {v for v in posiciones_ids if v}
            if ids_validos:
                encontradas = set(
                    Jugadora.objects.filter(id__in=ids_validos, equipo=partido.equipo).values_list('id', flat=True)
                )
                if encontradas != ids_validos:
                    ids_ajenos = ids_validos - encontradas
                    for jugadora_id in ids_ajenos:
                        log_intento_acceso_no_autorizado(request, 'Jugadora', jugadora_id)
                    return JsonResponse({'error': 'Jugadora no válida para este equipo'}, status=400)

            def update_rot(es_inicial):
                qs = RotacionSet.objects.filter(
                    partido=partido, set_numero=set_n, es_inicial=es_inicial
                )
                rot = qs.order_by('-id').first() if not es_inicial else qs.first()
                if not rot:
                    rot = RotacionSet(partido=partido, set_numero=set_n, es_inicial=es_inicial)
                
                rot.pos1_id = cd.get('pos1')
                rot.pos2_id = cd.get('pos2')
                rot.pos3_id = cd.get('pos3')
                rot.pos4_id = cd.get('pos4')
                rot.pos5_id = cd.get('pos5')
                rot.pos6_id = cd.get('pos6')
                rot.libero1_id = cd.get('libero1')
                rot.libero2_id = cd.get('libero2')
                rot.save()
                return rot

            # Durante el partido (sustituciones, edición en pista) solo se actualiza
            # la rotación actual; la alineación inicial (es_inicial=True) se conserva.
            if cd.get('solo_actual'):
                update_rot(False)
            else:
                update_rot(True)
                update_rot(False)
            
            return JsonResponse({'status': 'ok'})
        except Http404:
            raise
        except (OperationalError, InterfaceError):
            raise
        except Exception as e:
            logger.exception('Error inesperado en GuardarAlineacionInicialAPI')
            return JsonResponse({'error': ocultar_detalle_interno(e)}, status=400)

class RotarManualAPI(LoginRequiredMixin, View):
    @reintentar_en_error_transitorio()
    def post(self, request, partido_id):
        partido = _partido_del_entrenador(request, partido_id)
        data, error = _parsear_json(request)
        if error:
            return error

        form = RotarManualForm(data)
        if not form.is_valid():
            return _form_invalido(form)
        cd = form.cleaned_data

        set_n = cd.get('set_numero') or 1
        direccion = cd.get('direccion') or 'horario'
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
    @reintentar_en_error_transitorio()
    def post(self, request):
        data, error = _parsear_json(request)
        if error:
            return error

        form = ActualizarPosicionForm(data)
        if not form.is_valid():
            return _form_invalido(form)
        cd = form.cleaned_data

        try:
            jugadora = Jugadora.objects.get(id=cd['jugadora_id'], equipo__entrenador=request.user)
            jugadora.posicion = cd.get('posicion') or None
            jugadora.save()
            return JsonResponse({'status': 'ok', 'mensaje': f'Posición de {jugadora.nombre} actualizada'})
        except Jugadora.DoesNotExist:
            log_intento_acceso_no_autorizado(request, 'Jugadora', cd['jugadora_id'])
            return JsonResponse({'status': 'error', 'mensaje': 'Jugadora no encontrada'}, status=404)
