from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model

User = get_user_model()


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
