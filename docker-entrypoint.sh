#!/bin/sh
set -e

# Aplicar migraciones pendientes antes de arrancar gunicorn. En Cloud Run cada
# despliegue puede traer cambios de esquema (p.ej. nuevas columnas) y sin
# este paso la app falla con 500 al consultar modelos actualizados.
python manage.py migrate --noinput

exec gunicorn voley_stats_project.wsgi:application \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 2 \
    --threads 4 \
    --timeout 60
