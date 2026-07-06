# 🏐 MyVolleyStats — Estadísticas de Voleibol y Scout Táctico

Aplicación web Django para el **scouting estadístico en tiempo real** de equipos de voleibol. Pensada para el cuerpo técnico en banquillo: registra acciones por jugadora, gestiona rotaciones, calcula indicadores K1/K2 y genera informes post-partido en pantalla y en PDF.

**Idioma de la interfaz:** español (`es-es`) · **Zona horaria:** `Europe/Madrid`

---

## Tabla de contenidos

- [Características](#-características)
- [Instalación](#-instalación-local)
- [Variables de entorno](#-variables-de-entorno)
- [Flujo de trabajo](#-flujo-de-trabajo)
- [Modo partido](#-modo-partido)
- [Informes y PDFs](#-informes-y-pdfs)
- [Configuración](#-configuración)
- [Modelo de datos](#-modelo-de-datos)
- [Rutas principales](#-rutas-principales)
- [API REST](#-api-rest)
- [Glosario de métricas](#-glosario-de-métricas)
- [Estructura del proyecto](#-estructura-del-proyecto)
- [Stack tecnológico](#-stack-tecnológico)
- [Producción](#-producción)
- [Contribuir](#-contribuir)
- [Licencia](#-licencia)

---

## ✨ Características

### Gestión de club

| Módulo | Qué permite |
|--------|-------------|
| **Equipos** | CRUD con nombre, temporada y categoría (Benjamín → Senior) |
| **Jugadoras** | Dorsal, nombre, posición (Colocadora, Opuesta, Central, Receptora, Líbero), fecha de nacimiento |
| **Partidos** | Fecha, rival, local/visitante, lugar, modalidad (6×6 o Minivoley 4×4), estado finalizado |
| **Dashboard** | Partidos próximos e historial con acceso directo a scout, estadísticas y PDFs |

### Scout en vivo

| Función | Descripción |
|---------|-------------|
| **Modo Avanzado** | Matriz completa de fundamentos × calidades (`++`, `+`, `=`, `-`, `--`) |
| **Modo Rápido** | Cuadrícula por zonas, marcador compacto y sub-pestañas (Gameplay, Acciones, Score, Adjust, File, Settings) |
| **Marcador digital** | Puntos y sets en tiempo real con fin de set según reglamento (25/15, diff. ≥ 2) |
| **Fases K0/K1/K2** | Indicador automático de fase de juego (saque / recepción / defensa) |
| **Rotación** | Pizarra táctica, rotación manual y auto-rotación al ganar side-out |
| **Líberos** | Gestión de hasta 2 líberos (categorías Cadete en adelante) |
| **Sustituciones** | Registro de cambios sale/entra |
| **Deshacer** | Eliminar la última acción (con confirmación opcional) |
| **Exportación JSON** | Volcado de estadísticas por set desde el modo partido |

### Indicadores en banquillo

- Eficacia en recepción (side-out %) y eficacia en saque (breakpoint %)
- Tabla rápida por jugadora: saldo, puntos, errores, ataque, recepción, saque, bloqueo
- Líderes: MVP ataque, mejor sacadora, mejor atacante
- Alerta táctica (jugadora con peor rendimiento)
- Barras K1 (recepción + ataque) y K2 (saque + bloqueo + defensa)
- Matriz de eficacia por rotación R1–R6
- Registro de parones / tiempos muertos (localStorage)

### Informes post-partido

- **Estadísticas finales** en pantalla con filtro por set o partido completo
- Resumen por sets, gráficos Chart.js y box score detallado por jugadora
- Bloque **Destacadas**: máxima anotadora, líder de saque, mejor ataque (mín. 3 intentos)
- **PDF Resumen**: top 7 jugadoras + gráfico de origen de puntos
- **PDF Informe completo** (A4 apaisado): box score profesional por set + destacadas

### Preferencias de usuario

- Temas visuales (Oscuro, Azul Noche, Carbón)
- Modo scout por defecto (Rápido / Avanzado)
- Auto-rotación, vibración táctil y confirmación antes de deshacer
- Reglas del set editables por partido (puntos, set decisivo, sets para ganar)

---

## 🚀 Instalación local

### Requisitos previos

- Python **3.10+** (probado con 3.12)
- `pip`

### Pasos

```bash
# 1. Clonar el repositorio
git clone https://github.com/marioreyesapresa/voley_stats.git
cd voley_stats

# 2. Crear y activar entorno virtual
python -m venv env
source env/bin/activate        # macOS / Linux
# env\Scripts\activate       # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# → Edita .env y rellena SECRET_KEY con una clave segura

# 5. Aplicar migraciones
python manage.py migrate

# 6. Crear superusuario (acceso a la app y al admin)
python manage.py createsuperuser

# 7. Arrancar el servidor de desarrollo
python manage.py runserver
```

Accede en: **http://127.0.0.1:8000**

> No hay registro público de usuarios. Los accesos se crean con `createsuperuser` o desde el panel de administración Django.

---

## 🔐 Variables de entorno

Copia `.env.example` a `.env` y rellena los valores:

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `SECRET_KEY` | Clave secreta Django (50+ caracteres) | `django-insecure-...` |
| `DEBUG` | Modo debug (`True` en desarrollo) | `True` / `False` |
| `ALLOWED_HOSTS` | Hosts permitidos, separados por coma | `127.0.0.1,localhost` |
| `DATABASE_URL` | URL de base de datos (futuro prod.) | `sqlite:///db.sqlite3` |

> ⚠️ **Nunca subas el archivo `.env` al repositorio.** Ya está en `.gitignore`.

Genera una `SECRET_KEY` segura:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## 📋 Flujo de trabajo

```
Login → Dashboard
  → Crear equipo y jugadoras (/equipos/)
  → Crear partido
  → Modo partido
       1. Pizarra de rotación (alineación inicial)
       2. Scout en vivo (registrar acciones)
       3. Indicadores del set (métricas en banquillo)
  → Finalizar partido
  → Estadísticas finales + descargar PDFs
```

### Registro de una acción (modo avanzado)

1. Configura la alineación en la **Pizarra de Rotación**
2. Selecciona la jugadora en la barra superior
3. Pulsa el fundamento y la calidad (`++` … `--`)
4. El badge de fase (K0/K1/K2) se actualiza automáticamente
5. Al ganar un **side-out** (K1 → `++` en ataque), el sistema puede rotar la alineación
6. Al alcanzar el límite de puntos del set, aparece el **modal de fin de set**
7. Consulta los **Indicadores del Set** en la tercera pestaña

---

## 🎮 Modo partido

Ruta: `/partido/<id>/modo-partido/?tab=rotacion|scout|metricas`

### Pestaña 1 — Pizarra de rotación

- Cancha interactiva con **6 zonas** (voley) o **4 zonas en rombo** (minivoley)
- Asignación de titulares por zona y banquillo con plantilla completa
- Líberos en barra superior (dos rectángulos lado a lado; uno solo ocupa todo el ancho)
- Rotación inicial R1–R6 y estado inicial K0 (saque) / K1 (recepción)
- Botones: rotar horario, guardar alineación, limpiar pizarra

### Pestaña 2 — Scout en vivo

**Modo Avanzado:** marcador, fase, rotación activa, matriz de acciones, historial y deshacer.

**Modo Rápido:** orientado a tablet/móvil con cuadrícula por zonas y acceso rápido a marcador, ajustes y exportación.

**Acciones registrables:**

| Fundamento | Calidades |
|------------|-----------|
| Saque, Recepción, Colocación, Ataque, Bloqueo, Defensa | `++`, `+`, `=`, `-`, `--` |
| Error del rival, Punto del rival | — |
| Sustitución | sale / entra |

**Cálculo del marcador:**

- **Punto local:** `++` en saque, ataque o bloqueo · error del rival
- **Punto rival:** `--` en cualquier fundamento · punto del rival

### Pestaña 3 — Indicadores del set

Métricas en tiempo real alimentadas por la API `obtener-stats-set`. Incluye tabla rápida, líderes, alertas, barras K1/K2, matriz de rotaciones y enlaces a informes.

---

## 📊 Informes y PDFs

| Destino | URL | Contenido |
|---------|-----|-----------|
| Estadísticas finales (web) | `/partido/<id>/stats-final/` | Resumen, gráficos, box score, destacadas |
| PDF Resumen | `/partido/<id>/descargar-resumen/?set=global` | Top 7 + gráfico doughnut |
| PDF Informe completo | `/partido/<id>/descargar-informe-completo/?set=global` | Box score apaisado por set + destacadas |

Parámetro `set`: `global` (todo el partido) o número de set (`1`, `2`, …).

El servicio de reporting (`stats_app/services/reporting.py`) centraliza la agregación: marcador, side-out %, breakpoint %, box score por jugadora, informe rápido y destacadas.

---

## ⚙️ Configuración

### Página global (`/configuracion/`)

Preferencias guardadas en **localStorage** del navegador:

- Tema visual
- Modo scout por defecto
- Auto-rotación al ganar saque
- Vibración táctil
- Confirmar antes de deshacer
- Sidebar colapsada

### Reglas del partido (persistidas en BD)

Configurables desde **Ajustes** en modo partido o vía API:

| Campo | Por defecto | Descripción |
|-------|-------------|-------------|
| `puntos_por_set` | 25 | Límite de puntos en sets normales |
| `puntos_set_decisivo` | 15 | Límite en el set decisivo (p. ej. 5º) |
| `sets_para_ganar` | 3 | Sets necesarios para ganar el partido |

---

## 🗄️ Modelo de datos

```
User (entrenador)
  └── Equipo (1:N) — aislado por entrenador
        └── Jugadora (1:N)
        └── Partido (1:N)
              ├── RegistroEstadistica (acciones de scout)
              └── RotacionSet (alineación por set, inicial y actual)
```

`Equipo.entrenador` es la raíz del aislamiento multi-usuario: todo lo demás hereda la propiedad a través de sus relaciones (`equipo__entrenador`, `partido__equipo__entrenador`).

### Entidades principales

| Modelo | Campos clave |
|--------|--------------|
| **Equipo** | entrenador, nombre, temporada, categoría |
| **Jugadora** | equipo, dorsal, nombre, apellidos, posición, fecha_nacimiento |
| **Partido** | equipo, fecha, hora, rival, local, lugar, modalidad, finalizado, reglas del set |
| **RegistroEstadistica** | partido, jugadora, set, fase (K1/K2), acción, calidad, rotación activa |
| **RotacionSet** | partido, set, pos1–pos6, libero1/libero2, es_inicial |

---

## 🗺️ Rutas principales

### Administración

| Ruta | Descripción |
|------|-------------|
| `/` | Dashboard (próximos + historial) |
| `/equipos/` | Gestión de equipos y plantilla |
| `/equipo/nuevo/` · `/equipo/<id>/editar/` | CRUD equipo |
| `/jugadora/nueva/` · `/jugadora/<id>/editar/` | CRUD jugadora |
| `/partido/nuevo/` · `/partido/<id>/editar/` | CRUD partido |
| `/configuracion/` | Preferencias de UI |

### Scouting e informes

| Ruta | Descripción |
|------|-------------|
| `/partido/<id>/modo-partido/` | Scout en vivo |
| `/partido/<id>/stats-final/` | Estadísticas finales en pantalla |
| `/partido/<id>/descargar-resumen/` | PDF resumen |
| `/partido/<id>/descargar-informe-completo/` | PDF informe completo |

### Autenticación

| Ruta | Descripción |
|------|-------------|
| `/accounts/login/` | Inicio de sesión |
| `/accounts/logout/` | Cerrar sesión |
| `/admin/` | Panel de administración Django |

---

## 🔌 API REST

Todas las rutas bajo `/api/` devuelven JSON. La mayoría requieren sesión autenticada.

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/estadistica/registrar/` | Registrar acción de scout |
| `POST` | `/api/estadistica/eliminar/` | Eliminar última acción |
| `POST` | `/api/registrar-cambio/` | Registrar sustitución |
| `POST` | `/api/obtener-stats-set/` | Stats del set (KPIs, informe rápido, rotaciones) |
| `GET` | `/api/stats/<partido_id>/<set_n>/` | Stats por fundamento |
| `POST` | `/api/partido/<id>/config-set/` | Guardar reglas del set |
| `POST` | `/api/partido/<id>/finalizar/` | Marcar partido como finalizado |
| `GET` | `/api/rotacion/get/<partido_id>/?set=N` | Obtener alineación |
| `POST` | `/api/rotacion/inicial/<partido_id>/` | Guardar alineación |
| `POST` | `/api/rotacion/rotar/<partido_id>/` | Rotación manual |
| `POST` | `/api/jugadora/actualizar-posicion/` | Cambiar posición en plantilla |

---

## 📐 Glosario de métricas

| Término | Significado |
|---------|-------------|
| **K0** | Fase de saque (nuestro equipo saca) |
| **K1** | Fase de recepción / ataque tras recepción |
| **K2** | Fase de defensa tras saque rival |
| **Side-out %** | Eficacia en recepción: % de puntos ganados cuando recibimos |
| **Breakpoint %** | Eficacia en saque: % de puntos ganados cuando sacamos |
| **Saldo** | Puntos (`++`) menos errores (`--`) de una jugadora |
| **Box score** | Tabla completa de estadísticas por jugadora y fundamento |
| **Destacadas** | Máxima anotadora, líder de saque y mejor ataque del set/partido |

### Escala de calidad

| Símbolo | Interpretación habitual |
|---------|-------------------------|
| `++` | Excelente / punto directo |
| `+` | Bueno |
| `=` | Neutro |
| `-` | Malo |
| `--` | Error / punto rival |

---

## 🗂️ Estructura del proyecto

```
voley_stats/
├── voley_stats_project/          # Configuración Django
│   ├── settings.py               # Lee variables desde .env
│   ├── urls.py
│   └── wsgi.py
├── stats_app/                    # Aplicación principal
│   ├── models.py                 # Equipo, Jugadora, Partido, Estadística, Rotación
│   ├── urls.py
│   ├── admin.py
│   ├── services/
│   │   └── reporting.py          # Agregación de stats e informes
│   ├── views/
│   │   ├── administracion.py     # Dashboard, CRUD, configuración
│   │   ├── scouting.py           # Modo partido y APIs de stats
│   │   ├── rotaciones.py         # APIs de alineación y rotación
│   │   └── informes.py           # PDFs y estadísticas finales
│   ├── templatetags/
│   │   └── pdf_filters.py        # Filtros para plantillas PDF
│   ├── templates/stats_app/
│   │   ├── modo_partido.html     # Scout en vivo (UI principal)
│   │   ├── post_match_report.html
│   │   ├── informe_completo_pdf.html
│   │   └── informe_resumen_pdf.html
│   └── tests.py                  # Tests de aislamiento multi-entrenador
├── Dockerfile                    # Build multi-stage para Cloud Run
├── .dockerignore
├── .env.example
├── .gitignore
├── manage.py
└── requirements.txt
```

---

## 🛠️ Stack tecnológico

| Capa | Tecnología |
|------|------------|
| Backend | Django 6.x |
| Frontend | HTML + Tailwind CSS (CDN) + JavaScript vanilla |
| Base de datos | SQLite (desarrollo) |
| PDF | xhtml2pdf + ReportLab |
| Gráficos web | Chart.js |
| Gráficos PDF | QuickChart.io |
| Iconos | Lucide Icons (CDN) |
| Configuración | python-decouple |

---

## 🌐 Producción — Google Cloud Run + Neon (PostgreSQL)

La aplicación es **multi-entrenador**: cada `Equipo` pertenece a un usuario (`entrenador`), y todas las consultas (equipos, jugadoras, partidos, estadísticas, rotaciones) se filtran automáticamente por `request.user`. Un entrenador nunca puede ver ni modificar los datos de otro (comprobado con tests automáticos en `stats_app/tests.py`).

### Variables de entorno en producción

| Variable | Valor típico en Cloud Run |
|----------|---------------------------|
| `SECRET_KEY` | Secreto único, gestionado con **Secret Manager** |
| `DJANGO_DEBUG` | `False` |
| `ALLOWED_HOSTS` | `tu-servicio-xxxxx.a.run.app,tudominio.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://tu-servicio-xxxxx.a.run.app,https://tudominio.com` |
| `DATABASE_URL` | Cadena de conexión de **Neon** (`postgres://usuario:pass@host/db?sslmode=require`) |

Con `DJANGO_DEBUG=False`, `settings.py` activa automáticamente: `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_BROWSER_XSS_FILTER`, `SECURE_CONTENT_TYPE_NOSNIFF`, HSTS y cookies `HttpOnly`.

### Flujo de ramas (CI/CD)

| Rama | Uso | ¿Despliega a producción? |
|------|-----|--------------------------|
| **`develop`** | Desarrollo diario, pruebas y features | **No** |
| **`main`** | Código estable listo para producción | **Sí** (automático vía GitHub Actions) |

```bash
# Trabajar en develop (sin despliegues)
git checkout develop
# ... cambios, commits ...
git push origin develop

# Cuando esté listo para producción, fusionar en main
git checkout main
git pull origin main
git merge develop
git push origin main   # ← esto sí dispara el deploy a Cloud Run
```

### Despliegue paso a paso

```bash
# 1. Crear la base de datos en Neon y copiar su connection string (pooled)

# 2. Construir la imagen (el build usa un SECRET_KEY ficticio solo para collectstatic)
docker build -t voley-stats .

# 3. Probar en local con variables de producción
docker run -p 8080:8080 \
  -e SECRET_KEY="clave-real-de-produccion" \
  -e DJANGO_DEBUG=False \
  -e ALLOWED_HOSTS=127.0.0.1,localhost \
  -e DATABASE_URL="postgres://usuario:pass@host/db?sslmode=require" \
  -e PORT=8080 \
  voley-stats

# 4. Aplicar migraciones contra Neon (una vez, desde un job o localmente
#    apuntando a DATABASE_URL de producción)
DATABASE_URL="postgres://..." python manage.py migrate
DATABASE_URL="postgres://..." python manage.py createsuperuser

# 5. Desplegar en Cloud Run
gcloud run deploy voley-stats \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars DJANGO_DEBUG=False,ALLOWED_HOSTS=...,CSRF_TRUSTED_ORIGINS=...,EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend,EMAIL_HOST=smtp.gmail.com,EMAIL_PORT=587,EMAIL_HOST_USER=myvolleystats@gmail.com,EMAIL_USE_TLS=True,DEFAULT_FROM_EMAIL=myvolleystats@gmail.com \
  --set-secrets SECRET_KEY=voley-secret-key:latest,DATABASE_URL=voley-database-url:latest,EMAIL_HOST_PASSWORD=voley-email-password:latest
```

> Guarda `SECRET_KEY`, `DATABASE_URL` y `EMAIL_HOST_PASSWORD` en **Secret Manager** (`--set-secrets`), nunca como `--set-env-vars` en texto plano.

### Archivos de infraestructura

| Archivo | Función |
|---------|---------|
| `Dockerfile` | Build multi-stage (`builder` + `runtime`), imagen ligera basada en `python:3.12-slim`, sirve con Gunicorn en `$PORT` |
| `.dockerignore` | Excluye `env/`, `db.sqlite3`, `.env`, tests y metadatos del contexto de build |
| `requirements.txt` | Incluye `dj-database-url`, `psycopg[binary]`, `whitenoise`, `gunicorn` para producción |

### Migración de datos existentes a multi-entrenador

Si vienes de una versión sin `Equipo.entrenador`, las migraciones `0008`–`0010` añaden el campo de forma segura: primero nullable, después un backfill automático (asigna los equipos huérfanos al primer superusuario) y por último la restricción `NOT NULL`. Tras desplegar, revisa en `/admin/` que cada equipo tenga el entrenador correcto.

---

## 🤝 Contribuir

1. Haz fork del repositorio
2. Crea una rama: `git checkout -b feature/nueva-funcionalidad`
3. Haz commit con mensaje claro: `feat: descripción de la mejora`
4. Push: `git push origin feature/nueva-funcionalidad`
5. Abre un Pull Request

---

## 📄 Licencia

MIT License — Libre para uso personal y educativo.

---

## Notas

- Los **parones / tiempos muertos** se guardan solo en el navegador (localStorage), no en el servidor.
- El campo `entrenador_principal` existe en el modelo `Equipo` pero aún no tiene formulario en la UI web.
- Las rutas de reset de contraseña Django están disponibles pero sin plantillas personalizadas.
