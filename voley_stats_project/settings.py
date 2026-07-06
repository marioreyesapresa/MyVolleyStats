import dj_database_url
from pathlib import Path
from decouple import config, Csv

# ─────────────────────────────────────────────────────────────────────────────
# Rutas base
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# Seguridad — leídos desde variables de entorno (NUNCA hardcodeados aquí)
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY')

# Controlado por DJANGO_DEBUG. En Cloud Run / producción debe ser "False".
DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())

# Orígenes de confianza para peticiones POST/CSRF (necesario detrás de un
# dominio HTTPS de Cloud Run, p.ej. https://voley-stats-xxxxx-ew.a.run.app).
# Deben incluir el esquema (https://) y pueden llevar comodines de subdominio.
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

# ─────────────────────────────────────────────────────────────────────────────
# Aplicaciones instaladas
# ─────────────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'stats_app',
]

# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# WhiteNoise se coloca justo después de SecurityMiddleware para servir
# estáticos comprimidos y con cache-busting sin necesidad de Nginx.
# ─────────────────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'voley_stats_project.urls'

# ─────────────────────────────────────────────────────────────────────────────
# Plantillas
# ─────────────────────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'voley_stats_project.wsgi.application'

# ─────────────────────────────────────────────────────────────────────────────
# Base de datos
# - Local/desarrollo: SQLite por defecto si no hay DATABASE_URL.
# - Producción: Neon (PostgreSQL) leído desde DATABASE_URL vía dj-database-url.
#   Neon exige SSL, de ahí ssl_require=True (se ignora si es SQLite).
# ─────────────────────────────────────────────────────────────────────────────
DATABASE_URL = config('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')

DATABASES = {
    'default': dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        ssl_require=DATABASE_URL.startswith('postgres'),
    )
}

# ─────────────────────────────────────────────────────────────────────────────
# Validadores de contraseñas
# ─────────────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ─────────────────────────────────────────────────────────────────────────────
# Internacionalización
# ─────────────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'Europe/Madrid'
USE_I18N = True
USE_TZ = True

# ─────────────────────────────────────────────────────────────────────────────
# Archivos estáticos — servidos por WhiteNoise (sin Nginx) en Cloud Run
# ─────────────────────────────────────────────────────────────────────────────
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    # Se redefine STORAGES completo (Django lo exige): mantenemos el backend
    # de ficheros por defecto y solo cambiamos el de estáticos a WhiteNoise.
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Autenticación
# ─────────────────────────────────────────────────────────────────────────────
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'stats_app:dashboard'
LOGOUT_REDIRECT_URL = 'login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─────────────────────────────────────────────────────────────────────────────
# Seguridad en producción (Cloud Run)
#
# Estas directivas solo se activan cuando DEBUG=False, para no romper el
# flujo de desarrollo local en http://127.0.0.1:8000 (sin TLS).
# Cloud Run termina TLS en su proxy y reenvía la petición por HTTP interno
# añadiendo la cabecera X-Forwarded-Proto: https, de ahí SECURE_PROXY_SSL_HEADER.
# ─────────────────────────────────────────────────────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True

    # HSTS: fuerza HTTPS en el navegador durante 1 año una vez visitado.
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    X_FRAME_OPTIONS = 'DENY'
