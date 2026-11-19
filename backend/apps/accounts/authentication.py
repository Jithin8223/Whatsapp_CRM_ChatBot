from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from .models import AuthSession


class CookieJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        raw = request.COOKIES.get('crm_access')
        if not raw:
            return None
        token = self.get_validated_token(raw)
        user = self.get_user(token)
        if not AuthSession.objects.filter(id=token.get('sid'), user=user, expires_at__gt=timezone.now()).exists():
            raise AuthenticationFailed('Session expired. Please sign in again.')
        SessionAuthentication().enforce_csrf(request)
        return user, token
