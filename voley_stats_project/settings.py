import sys
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
    # Se coloca lo antes posible: una petición bloqueada por rate limit no
    # debe gastar ciclos en sesión/CSRF/auth ni tocar la base de datos.
    'stats_app.security.RateLimitMiddleware',
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
# Base de datos — Resiliencia frente a micro-cortes de red (Neon + Cloud Run)
# - Local/desarrollo: SQLite por defecto si no hay DATABASE_URL.
# - Producción: Neon (PostgreSQL) leído desde DATABASE_URL vía dj-database-url.
#   Neon exige SSL, de ahí ssl_require=True (se ignora si es SQLite).
#
# Neon cierra agresivamente las conexiones inactivas (auto-suspend / pooler) y
# Cloud Run puede sufrir micro-cortes de red entre contenedores. Sin las
# opciones de abajo, Django reutilizaría una conexión persistente ya muerta
# (conn_max_age) y el primer query tras el corte lanzaría un 500
# (OperationalError / InterfaceError: "server closed the connection").
#
#   - conn_max_age:      mantiene la conexión abierta 10 min entre peticiones
#                         para no pagar el coste de un TCP+TLS handshake nuevo
#                         en cada request (importante en Cloud Run).
#   - conn_health_checks: antes de reutilizar una conexión persistente, Django
#                         hace un ping (`SELECT 1`) y si ha muerto, la
#                         descarta y abre una nueva de forma transparente.
#                         Es la mitigación oficial de Django (4.1+) para este
#                         escenario exacto de "serverless Postgres" con
#                         conexiones persistentes.
#   - options.connect_timeout: evita que una petición se quede colgada
#                         indefinidamente si Neon no responde al abrir TCP.
# ─────────────────────────────────────────────────────────────────────────────
DATABASE_URL = config('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')
_ES_POSTGRES = DATABASE_URL.startswith('postgres')

DATABASES = {
    'default': dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=_ES_POSTGRES,
    )
}

if _ES_POSTGRES:
    DATABASES['default'].setdefault('OPTIONS', {})
    # Tiempo máximo para establecer la conexión TCP con Neon. Si la red está
    # inestable, fallamos rápido en vez de colgar el worker de gunicorn.
    DATABASES['default']['OPTIONS']['connect_timeout'] = 5
else:
    # SQLite (dev/tests): por defecto solo espera ~5s antes de lanzar
    # "database is locked" si dos conexiones escriben a la vez. Los tests de
    # concurrencia (stats_app.tests.ConcurrenciaYRaceConditionsTests) lanzan
    # peticiones reales desde varios hilos; se amplía el timeout para que
    # esperen su turno igual que lo haría Postgres/Neon con MVCC, en vez de
    # fallar por una limitación conocida de SQLite ajena a la lógica probada.
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS']['timeout'] = 20

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
# Caché — usada como almacén del Rate Limiting (stats_app.security)
#
# LocMemCache vive en la memoria del propio proceso/contenedor: no requiere
# Redis/Memcached externo. Limitación conocida y aceptada: en Cloud Run con
# min-instances > 1 o autoescalado el contador NO se comparte entre réplicas,
# por lo que el límite real es "N peticiones/minuto por IP y por instancia".
# Sigue siendo una mitigación eficaz contra fuerza bruta y scripts de abuso
# que golpean una URL pública descubierta, sin añadir infraestructura nueva.
# ─────────────────────────────────────────────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'rate-limit-cache',
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Rate Limiting — límites lógicos por IP (ver stats_app/security.py)
# Formato: (patrón de ruta, máx. peticiones, ventana en segundos)
# ─────────────────────────────────────────────────────────────────────────────
RATE_LIMIT_RULES = [
    # Login / registro: protección de fuerza bruta sobre credenciales.
    (r'^/accounts/login/', 10, 60),
    (r'^/accounts/register/', 10, 60),
    # APIs de scouting en vivo: alto volumen legítimo (clicks rápidos del
    # entrenador + reintentos automáticos del frontend), pero acotado para
    # frenar un abuso automatizado/DoS si se descubre la URL de Cloud Run.
    (r'^/api/', 240, 60),
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
#
# El backend con manifest (`CompressedManifestStaticFilesStorage`) exige que
# `collectstatic` se haya ejecutado antes: sin el `staticfiles.json` que
# genera, cualquier `{% static %}` lanza `ValueError: Missing staticfiles
# manifest entry`. Eso es aceptable —deseable, incluso— en producción (fuerza
# a no desplegar sin haber generado los assets con hash), pero rompería el
# desarrollo local y la suite de tests si se usara también con `DEBUG=True`,
# ya que nadie ejecuta `collectstatic` antes de un `manage.py test`. Por eso
# el backend de manifest solo se activa cuando `DEBUG=False`; en desarrollo
# se usa el `StaticFilesStorage` plano de Django, que sirve los ficheros
# directamente sin necesitar un manifest previo.
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
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage' if not DEBUG
            else 'django.contrib.staticfiles.storage.StaticFilesStorage'
        ),
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

# ─────────────────────────────────────────────────────────────────────────────
# Limpieza de cabeceras / no fuga de información técnica
#
# - Con DEBUG=False (obligatorio en producción) Django ya sustituye la
#   página de error técnica (traceback, versión de Django, SQL ejecutado,
#   variables de entorno) por una página genérica sin ningún detalle interno.
#   Las plantillas `stats_app/templates/404.html` y `500.html` personalizan
#   esa página genérica con la identidad visual de la app, sin revelar nada
#   más.
# - Las respuestas JSON de las APIs de scouting nunca serializan `str(exc)`
#   directamente al cliente: pasan por `security.ocultar_detalle_interno`,
#   que solo muestra el mensaje real en local (DEBUG=True) y siempre registra
#   el detalle completo en el log del servidor vía `logger.exception`.
# - La cabecera `Server` (que podría revelar la pila HTTP subyacente) la
#   gestiona el servidor WSGI de producción (gunicorn, ver Procfile/Dockerfile),
#   no Django, y gunicorn no expone la versión de Django en ninguna cabecera.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Logging — auditoría de seguridad para Cloud Run Logging
#
# `stats_app.security` registra en WARNING cada bloqueo de rate limit (429)
# y cada intento de acceso a un recurso ajeno vía IDOR (404 forzado), con la
# IP real del atacante (X-Forwarded-For) y el recurso/acción implicados. Se
# usa un StreamHandler explícito (en vez de confiar en el "handler de último
# recurso" de Python) para garantizar un formato consistente y que Cloud Run
# lo capture siempre por stdout/stderr, sin depender de que no haya otros
# handlers configurados en el proceso.
# ─────────────────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'seguridad': {
            'format': '[SECURITY] %(asctime)s %(levelname)s %(name)s: %(message)s',
        },
    },
    'handlers': {
        'console_seguridad': {
            'class': 'logging.StreamHandler',
            'formatter': 'seguridad',
        },
    },
    'loggers': {
        'stats_app.security': {
            'handlers': ['console_seguridad'],
            'level': 'WARNING',
            'propagate': False,
        },
        'stats_app.db_resilience': {
            'handlers': ['console_seguridad'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Solo en `manage.py test` — la suite de seguridad simula fuerza bruta
# (decenas de logins) y concurrencia real con hilos; el hasher de
# contraseñas de producción (PBKDF2, deliberadamente lento) multiplicaría
# el tiempo de CI sin aportar nada a lo que se está probando. No afecta a
# ningún entorno real: solo se activa cuando el proceso es `test`.
# ─────────────────────────────────────────────────────────────────────────────
if 'test' in sys.argv:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
