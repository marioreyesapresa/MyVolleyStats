from io import BytesIO
from django.shortcuts import get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from datetime import datetime
from ..models import Partido, Jugadora, RegistroEstadistica
from ..services.reporting import build_full_report

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
        stats_base = RegistroEstadistica.objects.filter(partido=partido)
        if set_n != 'global':
            stats_base = stats_base.filter(set_numero=set_n)
        
        # Get all players who actually played/disputed minutes in this match
        from ..models import RotacionSet
        rotaciones = RotacionSet.objects.filter(partido=partido)
        jugadoras_activas_ids = set()
        for rot in rotaciones:
            for field in ['pos1_id', 'pos2_id', 'pos3_id', 'pos4_id', 'pos5_id', 'pos6_id', 'libero1_id', 'libero2_id']:
                val = getattr(rot, field)
                if val:
                    jugadoras_activas_ids.add(val)

        fundamentos = ['SAQUE', 'RECEPCION', 'COLOCACION', 'ATAQUE', 'BLOQUEO', 'DEFENSA']
        jugadoras_stats = []
        j_ids = stats_base.values_list('jugadora_id', flat=True).distinct()
        
        for j in Jugadora.objects.filter(id__in=j_ids).filter(id__in=jugadoras_activas_ids):
            j_qs = stats_base.filter(jugadora=j)
            j_qs_f = j_qs.filter(accion__in=fundamentos)
            if j_qs_f.exists():
                pp = j_qs_f.filter(calidad='++').count()
                mm = j_qs_f.filter(calidad='--').count()
                t_vol = j_qs_f.count()
                
                j_data = {
                    'dorsal': j.dorsal, 'nombre': j.nombre,
                    'puntos': pp, 'errores': mm, 'balance': pp - mm,
                    'total': t_vol,
                    'efi_global': round(max(0, ((pp - mm) / t_vol) * 100)) if t_vol > 0 else 0,
                    'desglose': {}
                }
                for fund in fundamentos:
                    f_qs = j_qs.filter(accion=fund)
                    if f_qs.exists():
                        f_pp = f_qs.filter(calidad='++').count()
                        f_p = f_qs.filter(calidad='+').count()
                        f_m = f_qs.filter(calidad='-').count()
                        f_mm = f_qs.filter(calidad='--').count()
                        f_tot = f_pp + f_p + f_m + f_mm
                        j_data['desglose'][fund] = {
                            'pp': f_pp, 'p': f_p, 'm': f_m, 'mm': f_mm, 'total': f_tot,
                            'efi': round(max(0, ((f_pp - f_mm) / f_tot) * 100)) if f_tot > 0 else 0
                        }
                jugadoras_stats.append(j_data)
        
        jugadoras_stats.sort(key=lambda x: x['balance'], reverse=True)
        return jugadoras_stats

class DescargarResumenPDF(BaseInformePDFView):
    def get(self, request, pk):
        partido = get_object_or_404(Partido, pk=pk)
        set_n = request.GET.get('set', 'global')
        stats = self.get_stats_data(partido, set_n)
        
        merito = RegistroEstadistica.objects.filter(partido=partido, calidad='++').count()
        err_rival = RegistroEstadistica.objects.filter(partido=partido, accion='ERROR_RIVAL').count()
        
        context = {
            'partido': partido,
            'jugadoras_stats_list': stats[:7],
            'fecha': datetime.now().strftime("%d/%m/%Y"),
            'set_n': set_n,
            'chart_origen_url': f"https://quickchart.io/chart?c={{type:'doughnut',data:{{labels:['Mérito','Err.Rival'],datasets:[{{data:[{merito},{err_rival}],backgroundColor:['%2310b981','%233b82f6']}}]}},options:{{plugins:{{legend:{{position:'bottom'}}}}}}}}"
        }
        pdf = render_to_pdf('stats_app/informe_resumen_pdf.html', context)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Resumen_{partido.rival}.pdf"'
        return response

class DescargarInformeCompletoPDF(BaseInformePDFView):
    def get(self, request, pk):
        partido = get_object_or_404(Partido, pk=pk)
        set_n = request.GET.get('set', 'global')
        reporte = build_full_report(partido, set_n)

        context = {
            'partido': partido,
            'fecha': datetime.now().strftime("%d/%m/%Y"),
            'set_n': set_n,
            'resumen_sets': reporte['resumen_sets'],
            'detalle_sets': reporte['detalle_sets'],
            'destacadas': reporte['destacadas'],
        }
        pdf = render_to_pdf('stats_app/informe_completo_pdf.html', context)
        if pdf is None:
            return HttpResponse('Error al generar el PDF', status=500)
        response = HttpResponse(pdf, content_type='application/pdf')
        sufijo = f'_Set{set_n}' if set_n != 'global' else ''
        response['Content-Disposition'] = f'attachment; filename="Informe_Completo_{partido.rival}{sufijo}.pdf"'
        return response
