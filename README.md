# 🏐 Voley Stats — Scout Rotation Pro

Aplicación web Django para el **scouting estadístico en tiempo real** de equipos de voleibol femenino. Diseñada para el cuerpo técnico, permite registrar acciones, analizar rendimiento por fases de juego (K0/K1/K2) y gestionar rotaciones automáticamente.

---

## ✨ Características principales

- **Scout en Vivo** — Registro de acciones por jugadora (Saque, Recepción, Ataque, Bloqueo, Defensa, Colocación) con escala de calidad estándar (++, +, =, -, --)
- **Marcador Digital** — Marcador en tiempo real con validación de fin de set según reglamento FIVB (25/15 pts, diff. 2)
- **Fases de Juego** — Indicador automático K0 (Saque) / K1 (Recepción) / K2 (Defensa)
- **Rotación Automática** — Rota la alineación al ganar un side-out (K1 → K0)
- **Pizarra de Rotación** — Visualización táctica de las 6 zonas con gestión de titulares y suplentes
- **Indicadores del Set** — Líderes (MVP, saque, ataque), Alerta Táctica, Eficacia K1/K2, Matriz R1–R6, Registro de Parones
- **Informe Post-Partido** — Estadísticas completas por jugadora y fundamento con 5 niveles de calidad
- **Exportación PDF** — Resumen descargable por set o global

---

## 🚀 Instalación local

### Requisitos previos
- Python 3.10+
- pip

### Pasos

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/voley-stats.git
cd voley-stats

# 2. Crear y activar entorno virtual
python -m venv env
source env/bin/activate        # macOS/Linux
# env\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# → Edita .env y rellena SECRET_KEY con una clave segura

# 5. Aplicar migraciones
python manage.py migrate

# 6. Crear superusuario (admin)
python manage.py createsuperuser

# 7. Arrancar el servidor de desarrollo
python manage.py runserver
```

Accede en: **http://127.0.0.1:8000**

---

## 🔐 Variables de entorno

Copia `.env.example` a `.env` y rellena los valores:

| Variable | Descripción | Ejemplo |
|---|---|---|
| `SECRET_KEY` | Clave secreta Django (50+ chars) | `django-...` |
| `DEBUG` | Modo debug | `True` / `False` |
| `ALLOWED_HOSTS` | Hosts permitidos (coma) | `127.0.0.1,localhost` |

> ⚠️ **Nunca subas el archivo `.env` al repositorio.** Ya está en `.gitignore`.

Genera una nueva SECRET_KEY segura con:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## 🗂️ Estructura del proyecto

```
voley_stats/
├── voley_stats_project/      # Configuración Django
│   ├── settings.py           # Settings (lee .env)
│   ├── urls.py
│   └── wsgi.py
├── stats_app/                # Aplicación principal
│   ├── models.py             # Modelos: Equipo, Jugadora, Partido, Estadistica…
│   ├── views/                # Vistas organizadas por módulo
│   │   ├── scouting.py       # APIs de scout y estadísticas
│   │   ├── rotaciones.py     # API de rotación
│   │   └── informes.py       # Informe post-partido y PDF
│   ├── templates/            # HTML con Tailwind CSS
│   └── urls.py
├── .env.example              # Template de variables de entorno
├── .gitignore
├── manage.py
└── requirements.txt
```

---

## 🛠️ Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Django 6.x |
| Frontend | HTML + Tailwind CSS + Vanilla JS |
| Base de datos | SQLite (dev) / PostgreSQL (prod) |
| PDF | xhtml2pdf |
| Iconos | Lucide Icons (CDN) |

---

## 📋 Flujo de trabajo — Scout en Vivo

1. **Configura la alineación** en la Pizarra de Rotación
2. **Selecciona la jugadora** en la barra superior
3. **Registra la acción** (fundamento + calidad)
4. El **badge de fase** (K0/K1/K2) cambia automáticamente
5. Al ganar un **side-out** (K1 → `++`), el sistema rota automáticamente
6. Al finalizar el set (25-23, 26-24…), aparece el **modal de fin de set**
7. Consulta los **Indicadores del Set** en tiempo real

---

## 🤝 Contribuir

1. Haz fork del repositorio
2. Crea una rama: `git checkout -b feature/nueva-funcionalidad`
3. Haz commit: `git commit -m "feat: descripción clara"`
4. Push: `git push origin feature/nueva-funcionalidad`
5. Abre un Pull Request

---

## 📄 Licencia

MIT License — Libre para uso personal y educativo.
