import json
from io import BytesIO
from urllib.parse import quote
from django.shortcuts import get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, Http404
from django.template.loader import get_template
from django.utils import timezone
from xhtml2pdf import pisa
from datetime import datetime
from ..models import Partido, Jugadora, RegistroEstadistica, RotacionSet
from ..services.reporting import build_full_report, _rows_for, _fund_counts
from ..security import log_intento_acceso_no_autorizado


def _partido_del_entrenador(request, pk):
    try:
        return get_object_or_404(Partido, pk=pk, equipo__entrenador=request.user)
    except Http404:
        log_intento_acceso_no_autorizado(request, 'Partido', pk)
        raise


def render_to_pdf(template_src, context_dict={}):
    template = get_template(template_src)
    html  = template.render(context_dict)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
    if not pdf.err:
        return result.getvalue()
    return None

class BaseInformePDFView(LoginRequiredMixin, View):
    def get_stats_data(self, partido, set_n='global'):
        set_num = None if set_n == 'global' else set_n
        rows = _rows_for(partido, set_num)

        # Solo jugadoras que realmente disputaron minutos en el partido
        # (aparecen en alguna rotación de algún set).
        jugadoras_activas_ids = set()
        for rot in RotacionSet.objects.filter(partido=partido):
            for field in ['pos1_id', 'pos2_id', 'pos3_id', 'pos4_id', 'pos5_id', 'pos6_id', 'libero1_id', 'libero2_id']:
                val = getattr(rot, field)
                if val:
                    jugadoras_activas_ids.add(val)

        fundamentos = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
        fund_set = set(fundamentos)

        rows_por_jugadora = {}
        for r in rows:
            jid = r['jugadora_id']
            if jid is not None:
                rows_por_jugadora.setdefault(jid, []).append(r)

        j_ids = set(rows_por_jugadora.keys()) & jugadoras_activas_ids
        jugadoras_stats = []

        for j in Jugadora.objects.filter(id__in=j_ids):
            j_rows_f = [r for r in rows_por_jugadora.get(j.id, []) if r['accion'] in fund_set]
            if not j_rows_f:
                continue

            pp = sum(1 for r in j_rows_f if r['calidad'] == '++')
            mm = sum(1 for r in j_rows_f if r['calidad'] == '--')
            t_vol = len(j_rows_f)

            j_data = {
                'dorsal': j.dorsal, 'nombre': j.nombre,
                'puntos': pp, 'errores': mm, 'balance': pp - mm,
                'total': t_vol,
                'efi_global': round(max(0, ((pp - mm) / t_vol) * 100)) if t_vol > 0 else 0,
                'desglose': {}
            }
            for fund in fundamentos:
                c = _fund_counts(j_rows_f, fund)
                f_tot = c['pp'] + c['p'] + c['m'] + c['mm']
                if f_tot > 0:
                    j_data['desglose'][fund] = {
                        'pp': c['pp'], 'p': c['p'], 'm': c['m'], 'mm': c['mm'], 'total': f_tot,
                        'efi': round(max(0, ((c['pp'] - c['mm']) / f_tot) * 100))
                    }
            jugadoras_stats.append(j_data)

        jugadoras_stats.sort(key=lambda x: x['balance'], reverse=True)
        return jugadoras_stats

class DescargarResumenPDF(BaseInformePDFView):
    def get(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        set_n = request.GET.get('set', 'global')
        stats = self.get_stats_data(partido, set_n)

        rows = _rows_for(partido, None)
        merito = sum(1 for r in rows if r['calidad'] == '++')
        err_rival = sum(1 for r in rows if r['accion'] == 'ERROR_RIVAL')

        # El parámetro `c` de quickchart.io debe ir codificado como URL: un
        # carácter no-ASCII sin escapar (p.ej. la 'é' de "Mérito") hacía que
        # xhtml2pdf fallase al descargar la imagen (UnicodeEncodeError),
        # reintentando 3 veces contra la red antes de rendirse en cada PDF.
        chart_config = {
            'type': 'doughnut',
            'data': {
                'labels': ['Mérito', 'Err.Rival'],
                'datasets': [{'data': [merito, err_rival], 'backgroundColor': ['#10b981', '#3b82f6']}],
            },
            'options': {'plugins': {'legend': {'position': 'bottom'}}},
        }
        chart_origen_url = f"https://quickchart.io/chart?c={quote(json.dumps(chart_config))}"

        context = {
            'partido': partido,
            'jugadoras_stats_list': stats[:7],
            'fecha': datetime.now().strftime("%d/%m/%Y"),
            'set_n': set_n,
            'chart_origen_url': chart_origen_url,
        }
        pdf = render_to_pdf('stats_app/informe_resumen_pdf.html', context)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Resumen_{partido.rival}.pdf"'
        return response

class DescargarInformeCompletoPDF(BaseInformePDFView):
    def get(self, request, pk):
        partido = _partido_del_entrenador(request, pk)
        set_n = request.GET.get('set', 'global')

        if set_n == 'global' and partido.finalizado:
            pdf = self._pdf_completo_cacheado(partido)
        else:
            # El filtro por set concreto es barato de generar (una fracción
            # de los datos) y cambia de contenido con cada valor de `set`,
            # así que no merece la pena cachearlo: solo se cachea el informe
            # global de un partido ya finalizado.
            pdf = self._generar_pdf_completo(partido, set_n)

        if pdf is None:
            return HttpResponse('Error al generar el PDF', status=500)
        response = HttpResponse(pdf, content_type='application/pdf')
        sufijo = f'_Set{set_n}' if set_n != 'global' else ''
        response['Content-Disposition'] = f'attachment; filename="Informe_Completo_{partido.rival}{sufijo}.pdf"'
        return response

    def _generar_pdf_completo(self, partido, set_n):
        reporte = build_full_report(partido, set_n)
        context = {
            'partido': partido,
            'fecha': datetime.now().strftime("%d/%m/%Y"),
            'set_n': set_n,
            'resumen_sets': reporte['resumen_sets'],
            'detalle_sets': reporte['detalle_sets'],
            'detalle_total': reporte.get('detalle_total'),
            'destacadas': reporte['destacadas'],
        }
        return render_to_pdf('stats_app/informe_completo_pdf.html', context)

    def _pdf_completo_cacheado(self, partido):
        """Sirve el informe completo (global) desde caché en BD si el
        partido está finalizado y los datos no han cambiado desde que se
        generó; si no, lo genera una vez y lo guarda para las siguientes
        descargas. La invalidación compara el número de registros de
        estadística: cualquier alta o baja tras finalizar invalida la caché
        de forma automática, sin necesidad de señales ni tareas en segundo
        plano.
        """
        num_registros = RegistroEstadistica.objects.filter(partido=partido).count()
        cache_valida = (
            partido.informe_pdf_cache is not None
            and partido.informe_pdf_cache_num_registros == num_registros
        )
        if cache_valida:
            return bytes(partido.informe_pdf_cache)

        pdf = self._generar_pdf_completo(partido, 'global')
        if pdf is not None:
            Partido.objects.filter(pk=partido.pk).update(
                informe_pdf_cache=pdf,
                informe_pdf_cache_num_registros=num_registros,
                informe_pdf_cache_generado_en=timezone.now(),
            )
        return pdf
