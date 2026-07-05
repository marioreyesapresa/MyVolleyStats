# syntax=docker/dockerfile:1
#
# Imagen de producción para Google Cloud Run.
# Build multi-stage: la etapa "builder" compila dependencias con toolchain
# completo; la etapa final ("runtime") solo contiene las librerías compartidas
# necesarias en tiempo de ejecución, sin compiladores ni cabeceras -dev,
# manteniendo la imagen ligera y con menor superficie de ataque.

# ────────────────────────────────────────────────────────────────────────────
# Etapa 1 — builder
# ────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias de sistema para compilar wheels (cryptography, pillow, lxml)
# y las librerías gráficas que usan xhtml2pdf/reportlab/svglib para maquetar
# y rasterizar los PDFs de los informes (fuentes, Cairo/Pango, SVG, JPEG).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libffi-dev \
        libssl-dev \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Entorno virtual aislado que luego se copia íntegro a la imagen final
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# collectstatic necesita que Django pueda arrancar (SECRET_KEY definida),
# pero no toca la base de datos. Este valor es solo para el build y NO
# se propaga a la imagen final ni se usa nunca en tiempo de ejecución:
# en Cloud Run, la variable SECRET_KEY real siempre se inyecta como
# variable de entorno / secreto en el momento del despliegue.
ARG SECRET_KEY=build-time-placeholder-not-used-in-runtime
ENV SECRET_KEY=${SECRET_KEY} \
    DJANGO_DEBUG=True
RUN python manage.py collectstatic --noinput

# ────────────────────────────────────────────────────────────────────────────
# Etapa 2 — runtime (imagen final, ligera y sin herramientas de compilación)
# ────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Solo las librerías compartidas necesarias en ejecución (sin -dev/-dev headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libjpeg62-turbo \
        libffi8 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios: Cloud Run no lo exige, pero es buena práctica
# no ejecutar la aplicación como root dentro del contenedor.
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN chown -R app:app /app
USER app

# Cloud Run inyecta el puerto real en la variable $PORT (por defecto 8080);
# EXPOSE es documental, el bind real ocurre en el CMD de abajo.
EXPOSE 8080

# Health/arranque: 2 workers x 4 threads es un punto de partida razonable
# para instancias de Cloud Run con 1-2 vCPU; ajustar según carga real.
CMD gunicorn voley_stats_project.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60
