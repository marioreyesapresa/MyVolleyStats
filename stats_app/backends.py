from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Permite iniciar sesión con nombre de usuario o correo electrónico."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None or password is None:
            return None

        identificador = username.strip()
        usuario = None

        if '@' in identificador:
            usuario = User.objects.filter(email__iexact=identificador).first()
        if usuario is None:
            usuario = User.objects.filter(username__iexact=identificador).first()

        if usuario is None:
            return None
        if usuario.check_password(password) and self.user_can_authenticate(usuario):
            return usuario
        return None
