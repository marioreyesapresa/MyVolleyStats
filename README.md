# MyVolleyStats

Aplicación web de **scouting estadístico en tiempo real** para voleibol. Diseñada para el cuerpo técnico en banquillo (especialmente tablet): registro de acciones, rotaciones, indicadores K1/K2 e informes post-partido en web y PDF.

**Stack:** Django 6 · PostgreSQL (Neon) / SQLite · Tailwind CSS · PWA  
**Producción:** Google Cloud Run · despliegue automático desde `main`

---

## Características

- **Gestión de club** — equipos, plantilla, partidos y reglas configurables por encuentro
- **Scout en vivo** — Modo Rápido (cuadrícula por zonas, optimizado para iPad) y Modo Avanzado (matriz completa de fundamentos y calidades)
- **Marcador y rotaciones** — fases K0/K1/K2, líberos, sustituciones, fin de set automático
- **Indicadores en banquillo** — side-out %, breakpoint %, líderes, alertas tácticas y eficacia por rotación
- **Informes post-partido** — dos niveles de detalle (ver siguiente sección)
- **Multi-entrenador** — cada usuario solo accede a sus equipos y partidos

---

## Informes

| Informe | Web | PDF | Orientado a |
|---------|-----|-----|-------------|
| **Estadísticas Rápidas** | `/partido/<id>/stats-final/` | `/partido/<id>/descargar-informe-completo/` | Datos del modo rápido: zonas, saldo ++/−−, destacados, rotaciones |
| **Estadísticas Avanzadas** | `/partido/<id>/stats-avanzado/` | `/partido/<id>/descargar-informe-avanzado/` | Escala completa (++ / + / = / − / −−), Red, eficacia ponderada y complejos K1/K2 |
| **Resumen ejecutivo** | — | `/partido/<id>/descargar-resumen/` | Top jugadoras + origen de puntos |

Filtro común: `?set=global` o `?set=1`, `?set=2`, etc. Los PDF globales de partidos finalizados se cachean en base de datos para descarga instantánea.

---

## Inicio rápido

**Requisitos:** Python 3.10+

```bash
git clone https://github.com/marioreyesapresa/MyVolleyStats.git
cd MyVolleyStats

python -m venv env
source env/bin/activate          # Windows: env\Scripts\activate

pip install -r requirements.txt
cp .env.example .env             # Edita SECRET_KEY y variables necesarias

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Abre **http://127.0.0.1:8000** e inicia sesión. El registro público está activo (`ALLOW_PUBLIC_REGISTRATION=True`): en el login aparece **Crear cuenta**. Para cerrarlo, pon la variable a `False` y crea cuentas solo con `createsuperuser` o el panel admin.

### Tests

```bash
python manage.py test stats_app
python manage.py check --deploy   # con DJANGO_DEBUG=False y SECRET_KEY reales de prueba
```

---

## Configuración

Variables principales (detalle en `.env.example`):

| Variable | Descripción |
|----------|-------------|
| `SECRET_KEY` | Clave secreta Django |
| `DJANGO_DEBUG` | `True` en desarrollo, `False` en producción |
| `ALLOWED_HOSTS` | Hosts permitidos, separados por coma |
| `CSRF_TRUSTED_ORIGINS` | Orígenes HTTPS para CSRF (producción) |
| `DATABASE_URL` | Vacío → SQLite local; en producción → PostgreSQL (Neon) |
| `ALLOW_PUBLIC_REGISTRATION` | Registro público de entrenadores |
| `DJANGO_ADMIN_URL` | Path del admin (p.ej. `mvs-gestion-k7p2`) |
| `ADMIN_NOTIFY_EMAIL` | Destinatario de alertas 500 |
| `SENTRY_DSN` | Opcional: telemetría de errores (Sentry) |

---

## Escala de calidad (modo avanzado)

| Símbolo | Significado |
|---------|-------------|
| `++` | Excelente / punto directo |
| `+` | Bueno |
| `=` | En juego |
| `−` | Mejorable |
| `−−` | Error directo |
| **Red** | Toque de red → punto rival (solo modo avanzado) |

---

## Estructura

```
stats_app/
├── models.py              # Equipo, Jugadora, Partido, RegistroEstadistica, RotacionSet
├── services/reporting.py  # Agregación de estadísticas e informes
├── views/                 # administracion, scouting, rotaciones, informes
└── templates/stats_app/   # UI de scout, informes web y plantillas PDF
```

---

## Producción

Rama **`main`** despliega automáticamente a Cloud Run (GitHub Actions). El workflow ejecuta `manage.py test` y `check --deploy` **antes** de desplegar. Base de datos en **Neon** (PostgreSQL). Imagen Docker con Gunicorn; migraciones y `createcachetable` al arrancar el contenedor.

Flujo recomendado: desarrollo en `develop` → merge a `main` cuando esté listo para producción.

---

## Licencia

Uso personal y educativo. Consulta el repositorio para condiciones de uso.
