from ..models import Partido


def invalidar_cache_informes_partido(partido_id):
    """Invalida los PDF cacheados cuando cambian datos fuera de RegistroEstadistica."""
    Partido.objects.filter(pk=partido_id).update(
        informe_pdf_cache=None,
        informe_pdf_cache_num_registros=None,
        informe_pdf_cache_generado_en=None,
        informe_avanzado_pdf_cache=None,
        informe_avanzado_pdf_cache_num_registros=None,
        informe_avanzado_pdf_cache_generado_en=None,
    )
