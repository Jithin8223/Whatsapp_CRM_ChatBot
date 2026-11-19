from datetime import timedelta
from django.conf import settings
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken
from apps.tenants.models import Business, Membership, AuditEvent
from .models import User, AuthSession


class AuthThrottle(AnonRateThrottle):
    rate = '10/min'


class RegisterSerializer(serializers.Serializer):
    username = serializers.RegexField(r'^[\w.@+-]+$', max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, max_length=128)
    business_name = serializers.CharField(max_length=120)

    def validate(self, attrs):
        if User.objects.filter(username=attrs['username']).exists():
            raise serializers.ValidationError('Username is unavailable.')
        try:
            password_validation.validate_password(attrs['password'], User(username=attrs['username'], email=attrs['email']))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages})
        return attrs


def session_response(user, status=200):
    lifetime = timedelta(hours=8)
    auth_session = AuthSession.objects.create(user=user, expires_at=timezone.now() + lifetime)
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=lifetime)
    token['sid'] = str(auth_session.id)
    response = Response({'user': {'id': user.id, 'username': user.username}}, status=status)
    response.set_cookie('crm_access', str(token), max_age=int(lifetime.total_seconds()), httponly=True, secure=not settings.DEBUG, samesite='Lax', path='/')
    response['Cache-Control'] = 'no-store'
    return response


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CSRFView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'csrfToken': get_token(request)})


@method_decorator(csrf_protect, name='dispatch')
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    @transaction.atomic
    def post(self, request):
        data = RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        # Database unique constraint remains the final guard against concurrent registration.
        from django.db import IntegrityError
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=values['username'], email=values['email'], password=values['password'])
        except IntegrityError:
            raise serializers.ValidationError('Username is unavailable.')
        business = Business.objects.create(name=values['business_name'])
        Membership.objects.create(business=business, user=user, role='admin')
        AuditEvent.objects.create(business=business, actor=user, action='workspace.created')
        return session_response(user, 201)


@method_decorator(csrf_protect, name='dispatch')
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        class LoginSerializer(serializers.Serializer):
            username = serializers.CharField(max_length=150)
            password = serializers.CharField(max_length=128)
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, **data.validated_data)
        if user is None:
            return Response({'detail': 'Invalid username or password.'}, status=401)
        return session_response(user)


class LogoutView(APIView):
    def post(self, request):
        AuthSession.objects.filter(id=request.auth['sid'], user=request.user).delete()
        response = Response(status=204)
        response.delete_cookie('crm_access', path='/', samesite='Lax')
        return response


class MeView(APIView):
    def get(self, request):
        return Response({'whatsappEnabled': settings.WPPCONNECT_ENABLED, 'simulationEnabled': settings.DEBUG, 'user': {'id': request.user.id, 'username': request.user.username}, 'workspaces': [
            {'id': str(m.business_id), 'name': m.business.name, 'role': m.role}
            for m in request.user.memberships.select_related('business').order_by('created_at')
        ]})
