from datetime import timedelta
from django.conf import settings
from django.db import DatabaseError, transaction
from rest_framework.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from apps.tenants.models import AuditEvent, Business
from apps.tenants.permissions import WorkspaceMixin
from apps.notifications.events import publish
from .crypto import decrypt
from .models import Connection
from .serializers import ConnectionSerializer
from .outbox import queue_operation
from .services import operate_connection


class ConnectionThrottle(UserRateThrottle):
    scope = 'connection_write'
    rate = '30/min'


class ConnectionViewSet(WorkspaceMixin, viewsets.GenericViewSet):
    serializer_class = ConnectionSerializer

    def require_integration(self):
        self.require_admin()
        if not settings.WPPCONNECT_ENABLED:
            raise PermissionDenied('The optional WhatsApp integration is disabled.')
    def get_throttles(self):
        throttles = super().get_throttles()
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            throttles.append(ConnectionThrottle())
        return throttles

    def get_queryset(self):
        return Connection.objects.filter(business=self.business, removed_at__isnull=True, provider='wppconnect').order_by('created_at')

    def list(self, request, **kwargs):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    def retrieve(self, request, **kwargs):
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=False, methods=['get'], url_path='deployment-status')
    def deployment_status(self, request, **kwargs):
        return Response({'enabled': settings.WPPCONNECT_ENABLED})

    @transaction.atomic
    def create(self, request, **kwargs):
        self.require_integration()
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        Business.objects.select_for_update().get(id=self.business.id)
        if self.get_queryset().count() >= 5:
            return Response({'detail': 'Workspace connection limit reached.'}, status=400)
        item = data.save(business=self.business)
        AuditEvent.objects.create(business=self.business, actor=request.user, action='connection.created', object_id=str(item.id))
        return Response(self.get_serializer(item).data, status=201)

    def operation(self, request, operation):
        self.require_integration()
        queue_operation(self.get_object(), operation, request.user)
        return Response({'detail': 'Operation queued.'}, status=202)

    @action(detail=True, methods=['post'])
    def start(self, request, **kwargs):
        return self.operation(request, 'start')

    @action(detail=True, methods=['post'])
    def reconnect(self, request, **kwargs):
        return self.operation(request, 'start')

    @action(detail=True, methods=['post'])
    def disconnect(self, request, **kwargs):
        return self.operation(request, 'disconnect')

    @action(detail=True, methods=['post'])
    def remove(self, request, **kwargs):
        return self.operation(request, 'remove')

    @action(detail=True, methods=['patch'], url_path='automation')
    @transaction.atomic
    def automation(self, request, **kwargs):
        self.require_integration()
        item = self.get_queryset().select_for_update().get(pk=self.get_object().pk)

        class Input(serializers.Serializer):
            enabled = serializers.BooleanField(required=True)

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        enabled = data.validated_data['enabled']
        if enabled and not item.whatsapp_ready:
            raise serializers.ValidationError('Connect an authorized WhatsApp Business session before enabling AI replies.')
        item.ai_enabled = enabled
        item.save(update_fields=['ai_enabled'])
        AuditEvent.objects.create(
            business=self.business, actor=request.user,
            action='connection.ai_enabled' if enabled else 'connection.ai_disabled', object_id=str(item.id),
        )
        publish(self.business.id, 'connection.updated', item.id)
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['get'])
    def qr(self, request, **kwargs):
        self.require_integration()
        item = self.get_object()
        # While someone is pairing, read the provider directly instead of waiting for the
        # next 15-second sync, so a new QR appears within one poll. Never block on a lock
        # held by a running start/sync task.
        if item.state in ('connecting', 'qr') and item.token_encrypted and (
                not item.checked_at or timezone.now() - item.checked_at > timedelta(seconds=3)):
            try:
                with transaction.atomic():
                    Connection.objects.select_for_update(nowait=True).get(pk=item.pk)
                    operate_connection(self.business.id, item.pk, 'sync')
            except DatabaseError:
                pass
            item.refresh_from_db()
        value = decrypt(item.qr_encrypted) if item.qr_expires_at and item.qr_expires_at > timezone.now() else None
        state = item.state if item.state != 'connected' or item.whatsapp_ready else 'disconnected'
        response = Response({
            'qrcode': value, 'expires_at': item.qr_expires_at, 'state': state,
            'detail': item.status_detail, 'last_error': item.last_error, 'state_changed_at': item.state_changed_at,
        })
        response['Cache-Control'] = 'no-store'
        return response
