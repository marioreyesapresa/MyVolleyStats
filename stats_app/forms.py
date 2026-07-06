from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator

from .models import Equipo, Jugadora, RegistroEstadistica

User = get_user_model()

FIELD_CLASS = (
    'w-full px-5 py-3.5 bg-slate-900 border border-slate-800 rounded-2xl '
    'text-white focus:ring-2 focus:ring-emerald-500/50 focus:outline-none '
    'transition-all placeholder-slate-600 text-sm'
)


class LoginForm(AuthenticationForm):
    """Acepta usuario o correo en el campo `username` (vía EmailOrUsernameBackend)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuario o correo electrónico'
        self.fields['username'].widget.attrs.update({
            'class': FIELD_CLASS,
            'placeholder': 'usuario o tu@email.com',
            'autocomplete': 'username',
        })
        self.fields['password'].widget.attrs.update({
            'class': FIELD_CLASS,
            'autocomplete': 'current-password',
        })
        self.fields['password'].label = 'Contraseña'


class RegistroEntrenadorForm(UserCreationForm):
    """Alta de un nuevo entrenador. Cada usuario solo verá sus propios equipos."""

    email = forms.EmailField(
        required=True,
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-5 py-3.5 bg-slate-900 border border-slate-800 rounded-2xl text-white focus:ring-2 focus:ring-emerald-500/50 focus:outline-none transition-all placeholder-slate-600 text-sm',
            'placeholder': 'tu@email.com',
        }),
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_class = (
            'w-full px-5 py-3.5 bg-slate-900 border border-slate-800 rounded-2xl '
            'text-white focus:ring-2 focus:ring-emerald-500/50 focus:outline-none '
            'transition-all placeholder-slate-600 text-sm'
        )
        self.fields['username'].widget.attrs.update({
            'class': field_class,
            'placeholder': 'ej. mario_coach',
        })
        self.fields['username'].label = 'Nombre de usuario'
        self.fields['password1'].widget.attrs.update({'class': field_class})
        self.fields['password2'].widget.attrs.update({'class': field_class})
        self.fields['password1'].label = 'Contraseña'
        self.fields['password2'].label = 'Confirmar contraseña'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con este correo.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class JugadoraForm(forms.ModelForm):
    """Alta/edición de jugadoras. La fecha de nacimiento es opcional."""

    class Meta:
        model = Jugadora
        fields = ['equipo', 'nombre', 'apellidos', 'dorsal', 'posicion', 'fecha_nacimiento']

    def __init__(self, *args, entrenador=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha_nacimiento'].required = False
        if entrenador is not None:
            self.fields['equipo'].queryset = Equipo.objects.filter(entrenador=entrenador)


# ─────────────────────────────────────────────────────────────────────────────
# Formularios de validación de las APIs de scouting (Modo Partido)
#
# Blindaje contra OWASP A03 (Injection) / A04 (Insecure Design): todo payload
# JSON que llega a estas APIs pasa por aquí antes de tocar el ORM. Un ID de
# jugadora, de partido o de registro debe ser SIEMPRE un entero; un string
# tipo "1 OR 1=1", una lista `[1,2]` o un objeto `{}` se rechazan con un 400
# claro en vez de propagarse a una consulta o disparar una excepción 500.
# ─────────────────────────────────────────────────────────────────────────────

# 'K0' se usa en el frontend para la fase de saque propio, aunque no aparece
# en RegistroEstadistica.FASES (K1/K2 = complejos de recepción/defensa).
FASE_CHOICES = [('K0', 'K0')] + list(RegistroEstadistica.FASES)

DIRECCION_ROTACION_CHOICES = [('horario', 'Horario'), ('antihorario', 'Antihorario')]

# ─────────────────────────────────────────────────────────────────────────────
# Validación de rango de negocio (reglamento real de voleibol)
#
# No basta con "es un entero": un payload con set_numero=999999 o
# rotacion_num=-50 es sintácticamente válido pero incoherente con las reglas
# del juego. Estos límites acotan lo que el reglamento permite físicamente,
# cerrando la puerta a valores absurdos que podrían romper cálculos de
# marcador/rotación aguas abajo (p.ej. índices de lista, bucles, etc.).
# ─────────────────────────────────────────────────────────────────────────────
MAX_ROTACION_VOLEY = 6       # Zonas 1-6 (FIVB): pista completa de 6 jugadoras.
MAX_ROTACION_MINIVOLEY = 4   # Zonas 1-4: minivoley se juega 4 contra 4.
MAX_SETS_PARA_GANAR = 5      # Ningún formato oficial supera el "al mejor de 5".
MAX_PUNTOS_SET = 50          # Generoso frente a los 25/15 estándar, pero acota
                              # tie-breaks extremos sin permitir valores absurdos.
MIN_SET_NUMERO = 1
MAX_SET_NUMERO = (MAX_SETS_PARA_GANAR * 2) - 1  # Set decisivo máximo posible: 9.


class FormConValidacionDeModalidad(forms.Form):
    """Base para formularios cuyo rango válido de `rotacion_num` depende de
    si el partido es VOLEY (zonas 1-6) o MINIVOLEY (zonas 1-4).

    La vista conoce la modalidad recién después de resolver `partido_id`
    (para evitar filtrar existencia de partidos ajenos antes de aislar por
    entrenador), así que `modalidad` se inyecta de forma opcional en el
    `__init__`. Si no se indica, se aplica el límite más permisivo (VOLEY)
    para no bloquear al llamante que aún no conoce el contexto del partido.
    """

    def __init__(self, *args, modalidad=None, **kwargs):
        self.modalidad = modalidad
        super().__init__(*args, **kwargs)

    def clean_rotacion_num(self):
        valor = self.cleaned_data.get('rotacion_num')
        if valor is not None and self.modalidad == 'MINIVOLEY' and valor > MAX_ROTACION_MINIVOLEY:
            raise forms.ValidationError(
                f'rotacion_num fuera de rango para minivoley (máx. {MAX_ROTACION_MINIVOLEY}).'
            )
        return valor


class IdField(forms.IntegerField):
    """IntegerField endurecido para identificadores de payloads JSON.

    `forms.IntegerField` ya rechaza cadenas no numéricas, pero si el atacante
    envía un tipo no escalar (lista/dict) como valor, algunos backends de
    Django pueden lanzar `TypeError` en vez de `ValidationError` antes de
    llegar a `to_python`. Se corta aquí explícitamente devolviendo siempre
    un error de validación limpio (→ 400, nunca 500).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('min_value', 1)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if isinstance(value, (list, dict, set, tuple, bool)):
            raise forms.ValidationError('Identificador no válido: se esperaba un entero.')
        return super().to_python(value)


class RegistrarAccionForm(FormConValidacionDeModalidad):
    """Payload de POST /api/estadistica/registrar/."""

    partido_id = IdField()
    jugadora_id = IdField(required=False)
    fase = forms.ChoiceField(choices=FASE_CHOICES, required=False)
    accion = forms.ChoiceField(choices=RegistroEstadistica.ACCIONES)
    calidad = forms.ChoiceField(choices=RegistroEstadistica.CALIDADES, required=False)
    set_numero = IdField(required=False, initial=1, max_value=MAX_SET_NUMERO)
    rotacion_num = IdField(required=False, max_value=MAX_ROTACION_VOLEY, initial=1)


class RegistrarCambioForm(FormConValidacionDeModalidad):
    """Payload de POST /api/registrar-cambio/."""

    partido_id = IdField()
    sale_id = IdField()
    entra_id = IdField()
    set_numero = IdField(required=False, initial=1, max_value=MAX_SET_NUMERO)
    rotacion_num = IdField(required=False, max_value=MAX_ROTACION_VOLEY, initial=1)


class EliminarAccionForm(forms.Form):
    """Payload de POST /api/estadistica/eliminar/."""

    id = IdField()


class ObtenerStatsSetForm(forms.Form):
    """Payload de POST /api/obtener-stats-set/."""

    partido_id = IdField()
    set_numero = IdField(required=False, max_value=MAX_SET_NUMERO, initial=1)


class ConfigSetForm(forms.Form):
    """Payload de POST /api/partido/<id>/config-set/.

    Límites alineados con el reglamento real (FIVB): un set se juega
    habitualmente a 25 puntos (15 en el decisivo) y ningún formato oficial
    supera el "al mejor de 5" sets. Se deja margen sobre esos estándares
    para admitir variantes de liga/categoría, pero sin aceptar valores
    gigantes o incoherentes que solo tendrían sentido como ataque.
    """

    puntos_por_set = forms.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_PUNTOS_SET)]
    )
    puntos_set_decisivo = forms.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_PUNTOS_SET)]
    )
    sets_para_ganar = forms.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_SETS_PARA_GANAR)]
    )


class AlineacionInicialForm(forms.Form):
    """Payload de POST /api/rotacion/inicial/<partido_id>/."""

    set_numero = IdField(required=False, max_value=MAX_SET_NUMERO, initial=1)
    solo_actual = forms.BooleanField(required=False)
    pos1 = IdField(required=False)
    pos2 = IdField(required=False)
    pos3 = IdField(required=False)
    pos4 = IdField(required=False)
    pos5 = IdField(required=False)
    pos6 = IdField(required=False)
    libero1 = IdField(required=False)
    libero2 = IdField(required=False)


class RotarManualForm(forms.Form):
    """Payload de POST /api/rotacion/rotar/<partido_id>/."""

    set_numero = IdField(required=False, max_value=MAX_SET_NUMERO, initial=1)
    direccion = forms.ChoiceField(choices=DIRECCION_ROTACION_CHOICES, required=False, initial='horario')


class ActualizarPosicionForm(forms.Form):
    """Payload de POST /api/jugadora/actualizar-posicion/."""

    jugadora_id = IdField()
    posicion = forms.ChoiceField(choices=Jugadora.POSICIONES, required=False)
