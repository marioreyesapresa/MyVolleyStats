#!/bin/sh
set -e

# Migraciones + tabla de caché (rate limit compartido entre réplicas).
# createcachetable es idempotente si la tabla ya existe.
python manage.py migrate --noinput
python manage.py createcachetable 2>/dev/null || true

exec gunicorn voley_stats_project.wsgi:application \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 2 \
    --threads 4 \
    --timeout 60
