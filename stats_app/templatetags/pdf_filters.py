from django import template

register = template.Library()


@register.filter
def pdf_cell(value, show_zero=False):
    """Celda numérica: 0 → guion (salvo fila de totales)."""
    if value is None or value == '':
        return '—'
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    if show_zero in (True, 'true', '1', 1):
        if num == int(num):
            return int(num)
        return value
    if num == 0:
        return '—'
    if num == int(num):
        return int(num)
    return value


@register.filter
def pdf_efic_pct(value):
    """Convierte ratio 0.818 → 82%."""
    if value is None:
        return '—'
    try:
        pct = round(float(value) * 100)
    except (TypeError, ValueError):
        return '—'
    return f'{pct}%'


@register.filter
def pdf_saldo(value, show_zero=False):
    """Saldo: muestra signo; 0 → guion salvo totales."""
    if value is None:
        return '—'
    try:
        num = int(value)
    except (TypeError, ValueError):
        return value
    if num == 0 and show_zero not in (True, 'true', '1', 1):
        return '—'
    if num > 0:
        return f'+{num}'
    return str(num)
