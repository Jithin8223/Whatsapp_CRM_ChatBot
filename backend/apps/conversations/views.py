from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from uuid import UUID
from rest_framework import serializers, viewsets, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle
from apps.tenants.models import Membership, AuditEvent
from apps.tenants.permissions import WorkspaceMixin
from apps.whatsapp.models import Connection
from .models import Contact, Conversation, ConversationView, FollowUp, Message, MessageAttachment
from . import crm
from .serializers import ContactSerializer, ConversationSerializer, MessageSerializer
from .services import queue_message, enqueue, cancel_ai_replies
from rest_framework.exceptions import PermissionDenied
from apps.notifications.events import publish
from apps.ai_engine.models import AISettings, ReplyJob


def attachment_response(attachment, inline=False, cross_origin=False):
    """Authenticated streaming of a private file. Inline only for images/PDF, sandboxed."""
    from urllib.parse import quote
    from django.http import HttpResponse
    from .attachments import read
    response = HttpResponse(read(attachment), content_type=attachment.content_type)
    disposition = 'inline' if inline else 'attachment'
    response['Content-Disposition'] = f"{disposition}; filename*=UTF-8''{quote(attachment.filename)}"
    response['Content-Length'] = str(attachment.size)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    # The website widget fetches from the customer's own site (CORS + guest token).
    response['Cross-Origin-Resource-Policy'] = 'cross-origin' if cross_origin else 'same-origin'
    return response


class ContactViewSet(WorkspaceMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ContactSerializer

    def get_queryset(self):
        query = self.request.query_params.get('search', '')[:120]
        return Contact.objects.filter(business=self.business).filter(Q(display_name__icontains=query) | Q(whatsapp_id__icontains=query)).order_by('display_name', 'id')

    @transaction.atomic
    def create(self, request, **kwargs):
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        item, created = Contact.objects.get_or_create(business=self.business, whatsapp_id=values['whatsapp_id'], defaults={'display_name': values.get('display_name', ''), 'phone': values['whatsapp_id'].split('@')[0] if values['whatsapp_id'].endswith('@c.us') else ''})
        return Response(self.get_serializer(item).data, status=201 if created else 200)

    def partial_update(self, request, **kwargs):
        item = self.get_object()
        class Input(serializers.Serializer):
            display_name = serializers.CharField(max_length=120)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        item.display_name = data.validated_data['display_name']
        item.save(update_fields=['display_name', 'updated_at'])
        return Response(self.get_serializer(item).data)


class AttachmentUploadThrottle(UserRateThrottle):
    scope = 'attachment_upload'
    rate = '30/min'


class ConversationViewSet(WorkspaceMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ConversationSerializer

    def get_throttles(self):
        throttles = super().get_throttles()
        if self.action == 'attachments' and self.request.method == 'POST':
            throttles.append(AttachmentUploadThrottle())
        return throttles

    def get_queryset(self):
        result = Conversation.objects.filter(business=self.business, contact__business=self.business, connection__business=self.business).select_related('contact', 'last_message', 'connection').order_by('-last_activity_at', '-id')
        if not settings.DEBUG:
            result = result.exclude(connection__provider=Connection.SIMULATION_PROVIDER)
        if self.action == 'retrieve':
            result = result.annotate(ai_pending=Exists(ReplyJob.objects.filter(
                message__conversation_id=OuterRef('pk'), message__business=self.business,
            state__in=('queued', 'running', 'draft'),
            )))
        query = self.request.query_params.get('search', '')[:120]
        lookup = Q(contact__display_name__icontains=query) | Q(contact__whatsapp_id__icontains=query)
        import re
        guest = re.fullmatch(r'(?:guest\s*)?#?(\d{1,18})', query.strip(), re.I)
        if guest:
            lookup |= Q(contact__guest_number=int(guest[1]))
        result = result.filter(lookup)
        status = self.request.query_params.get('status')
        if status == 'pending':
            result = result.filter(status='open', reply_mode='waiting')
        elif status == 'open':
            result = result.filter(status='open').exclude(reply_mode='waiting')
        elif status == 'closed':
            result = result.filter(status='closed')
        channel = self.request.query_params.get('channel')
        if channel == 'website':
            result = result.filter(connection__provider=Connection.WEBSITE_PROVIDER)
        elif channel == 'whatsapp':
            result = result.exclude(connection__provider=Connection.WEBSITE_PROVIDER)
        if self.request.query_params.get('contact'):
            from uuid import UUID
            try:
                result = result.filter(contact_id=UUID(self.request.query_params['contact']))
            except ValueError:
                raise serializers.ValidationError('Invalid contact identifier.')
        crm_status = self.request.query_params.get('crm_status')
        if crm_status in dict(Conversation.CRM_STATUSES):
            result = result.filter(crm_status=crm_status)
        assignee = self.request.query_params.get('assignee')
        if assignee == 'me':
            result = result.filter(assigned_agent=self.request.user)
        elif assignee == 'none':
            result = result.filter(assigned_agent__isnull=True)
        elif assignee and assignee.isdigit():
            result = result.filter(assigned_agent_id=int(assignee))
        latest_view = ConversationView.objects.filter(conversation=OuterRef('pk')).order_by('-last_viewed_at')
        pending = FollowUp.objects.filter(conversation=OuterRef('pk'), status='pending')
        return result.select_related('assigned_agent').annotate(
            last_viewed_by_name=Subquery(latest_view.values('user__username')[:1]),
            last_viewed_at=Subquery(latest_view.values('last_viewed_at')[:1]),
            pending_followups=Coalesce(Subquery(pending.order_by().values('conversation').annotate(n=Count('id')).values('n')[:1]), 0),
            next_followup_date=Subquery(pending.order_by('due_date', 'due_time').values('due_date')[:1]),
        )

    def create(self, request, **kwargs):
        class Input(serializers.Serializer):
            connection = serializers.UUIDField()
            contact = serializers.UUIDField()
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        connections = Connection.objects.filter(provider='wppconnect')
        connection = get_object_or_404(connections, id=data.validated_data['connection'], business=self.business, removed_at__isnull=True)
        contact = get_object_or_404(Contact, id=data.validated_data['contact'], business=self.business, website_visitor_id__isnull=True)
        if contact.whatsapp_id.endswith('@simulation'):
            raise serializers.ValidationError('Simulation contacts cannot use live WhatsApp connections.')
        item, created = Conversation.objects.get_or_create(business=self.business, connection=connection, contact=contact)
        return Response(self.get_serializer(item).data, status=201 if created else 200)

    def _fresh(self, item_id):
        return self.get_serializer(self.get_queryset().get(id=item_id)).data

    @transaction.atomic
    def partial_update(self, request, **kwargs):
        """Backwards-compatible PATCH; each field maps to one explicit workflow action."""
        item_id = self.get_object().id

        class Input(serializers.Serializer):
            status = serializers.ChoiceField(choices=['open', 'closed'], required=False)
            reply_mode = serializers.ChoiceField(choices=['manual', 'ai', 'assisted'], required=False)
            assigned_agent = serializers.IntegerField(allow_null=True, required=False)
            crm_status = serializers.ChoiceField(choices=[value for value, _ in Conversation.CRM_STATUSES], required=False)
            internal_note = serializers.CharField(max_length=4000, allow_blank=True, required=False)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if 'assigned_agent' in values:
            target = values['assigned_agent']
            if self.member.role != 'admin':
                if target != request.user.id:
                    raise PermissionDenied('Agents can only claim conversations for themselves.')
                crm.claim(self.business, self.member, item_id)
            else:
                crm.assign(self.business, self.member, item_id, target)
        if 'reply_mode' in values:
            if values['reply_mode'] == 'manual':
                crm.takeover(self.business, self.member, item_id)
            else:
                crm.resume_ai(self.business, self.member, item_id, values['reply_mode'])
        if 'crm_status' in values:
            crm.set_crm_status(self.business, self.member, item_id, values['crm_status'])
        if 'status' in values or 'internal_note' in values:
            item = self.get_queryset().select_for_update(of=('self',)).get(id=item_id)
            if 'status' in values and item.assigned_agent_id not in (None, request.user.id) and self.member.role != 'admin':
                raise PermissionDenied('This conversation is assigned to another team member.')
            if values.get('status') == 'closed':
                cancel_ai_replies(item)
            for key in ('status', 'internal_note'):
                if key in values:
                    setattr(item, key, values[key])
            item.save(update_fields=[key for key in ('status', 'internal_note') if key in values])
            AuditEvent.objects.create(business=self.business, actor=request.user, action='conversation.updated', object_id=str(item.id))
            publish(self.business.id, 'conversation.updated', item.id)
        return Response(self._fresh(item_id))

    @action(detail=True, methods=['post'], url_path='view')
    def record_view(self, request, **kwargs):
        """Record that the current member opened this conversation (never claims it)."""
        crm.record_view(self.business, self.get_object(), request.user)
        return Response(status=204)

    @action(detail=True, methods=['get'], url_path='views')
    def view_history(self, request, **kwargs):
        self.require_admin()
        rows = ConversationView.objects.filter(business=self.business, conversation=self.get_object()).select_related('user').order_by('-last_viewed_at')
        return Response([{
            'user_id': row.user_id, 'username': row.user.username, 'first_viewed_at': row.first_viewed_at,
            'last_viewed_at': row.last_viewed_at, 'view_count': row.view_count,
        } for row in rows])

    @action(detail=True, methods=['post'])
    def claim(self, request, **kwargs):
        item = crm.claim(self.business, self.member, self.get_object().id)
        return Response(self._fresh(item.id))

    @action(detail=True, methods=['post'])
    def takeover(self, request, **kwargs):
        item = crm.takeover(self.business, self.member, self.get_object().id)
        return Response(self._fresh(item.id))

    @action(detail=True, methods=['post'], url_path='resume-ai')
    def resume_ai(self, request, **kwargs):
        item = crm.resume_ai(self.business, self.member, self.get_object().id)
        return Response(self._fresh(item.id))

    @action(detail=True, methods=['get', 'delete'], url_path='guest-data')
    @transaction.atomic
    def guest_data(self, request, **kwargs):
        self.require_admin()
        item = self.get_object()
        if item.connection.channel != 'website':
            raise serializers.ValidationError('Guest data management applies to website conversations.')
        item = Conversation.objects.select_for_update().get(id=item.id, business=self.business)
        if request.method == 'GET':
            messages = item.messages.filter(business=self.business).order_by('created_at', 'id')
            return Response({'guest_id': item.contact.guest_label, 'display_name': item.contact.display_name,
                'mobile': item.contact.crm_phone, 'email': item.contact.crm_email, 'messages': [
                {'direction': message.direction, 'content': message.content, 'created_at': message.created_at}
                for message in messages.iterator()
            ]}, headers={'Content-Disposition': 'attachment; filename="guest-conversation.json"'})
        contact = item.contact
        item_id = item.id
        item.delete()
        if not contact.conversations.exists():
            contact.delete()
        AuditEvent.objects.create(business=self.business, actor=request.user, action='guest.deleted', object_id=str(item_id))
        publish(self.business.id, 'conversation.updated', item_id)
        return Response(status=204)

    @action(detail=True, methods=['post'])
    def read(self, request, **kwargs):
        """A team member viewed the chat: clear the CRM unread count and stamp incoming messages
        as seen by the team. Internal only — never sent to the customer as a read receipt."""
        from django.utils import timezone
        item = self.get_object()
        seen = Message.objects.filter(business=self.business, conversation=item, direction='inbound',
                                      seen_at__isnull=True).update(seen_at=timezone.now())
        if Conversation.objects.filter(id=item.id, business=self.business, unread_count__gt=0).update(unread_count=0) or seen:
            publish(self.business.id, 'conversation.updated', item.id)
        return Response(status=204)

    @action(detail=True, methods=['get', 'post'])
    @transaction.atomic
    def messages(self, request, **kwargs):
        item = self.get_object()
        if request.method == 'GET':
            # Newest-first pages; UI displays each loaded window chronologically.
            page = self.paginate_queryset(item.messages.filter(business=self.business).select_related('attachment__uploaded_by').order_by('-created_at', '-id'))
            # Automatic replies use their ReplyJob's id (the inbound message) as client_id.
            sources = dict(ReplyJob.objects.filter(
                message_id__in=[row.client_id for row in page if row.automated and row.client_id],
                message__business=self.business,
            ).values_list('message_id', 'source'))
            return self.get_paginated_response(MessageSerializer(page, many=True, context={'reply_sources': sources}).data)
        class Input(serializers.Serializer):
            content = serializers.CharField(max_length=4096)
            client_id = serializers.UUIDField()
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        item = Conversation.objects.select_for_update().get(id=item.id, business=self.business)
        if item.assigned_agent_id not in (None, request.user.id) and self.member.role != 'admin':
            raise PermissionDenied('This conversation is assigned to another agent.')
        if item.messages.filter(client_id=data.validated_data['client_id']).exists():
            message, _ = queue_message(
                self.business.id, item.id, data.validated_data['client_id'], data.validated_data['content'],
                sender_type=self.member.role,
            )
            return Response(MessageSerializer(message).data, status=200)
        if item.reply_mode != 'manual' or not item.assigned_agent_id:
            # Sending a manual reply is a human takeover; an existing owner is kept.
            cancel_ai_replies(item)
            item.reply_mode = 'manual'
            item.assigned_agent_id = item.assigned_agent_id or request.user.id
            if item.crm_status == 'unassigned':
                item.crm_status = 'open'
            item.save(update_fields=['reply_mode', 'assigned_agent', 'crm_status'])
        message, created = queue_message(self.business.id, item.id, data.validated_data['client_id'], data.validated_data['content'], sender_type=self.member.role)
        AuditEvent.objects.create(business=self.business, actor=request.user, action='message.queued', object_id=str(message.id))
        return Response(MessageSerializer(message).data, status=201 if created else 200)

    @action(detail=True, methods=['get', 'post'], url_path='attachments')
    def attachments(self, request, **kwargs):
        from .attachments import storage, store
        from .serializers import attachment_data
        item = self.get_object()
        if request.method == 'GET':
            rows = MessageAttachment.objects.filter(business=self.business, conversation=item).select_related('uploaded_by').order_by('-created_at')
            return Response([attachment_data(row) for row in rows[:200]])

        class Input(serializers.Serializer):
            client_id = serializers.UUIDField()
            caption = serializers.CharField(max_length=1000, allow_blank=True, required=False)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        client_id = data.validated_data['client_id']
        existing = Message.objects.filter(conversation=item, client_id=client_id).select_related('attachment__uploaded_by').first()
        if existing:  # retried upload: never store or send twice
            return Response(MessageSerializer(existing).data, status=200)
        if item.assigned_agent_id not in (None, request.user.id) and self.member.role != 'admin':
            raise PermissionDenied('This conversation is assigned to another agent.')
        values = store(self.business.id, item.id, request.FILES.get('file'))
        try:
            with transaction.atomic():
                locked = Conversation.objects.select_for_update().get(id=item.id, business=self.business)
                if locked.reply_mode != 'manual' or not locked.assigned_agent_id:
                    # Sending a file is a human reply: take over, keeping any existing owner.
                    cancel_ai_replies(locked)
                    locked.reply_mode = 'manual'
                    locked.assigned_agent_id = locked.assigned_agent_id or request.user.id
                    if locked.crm_status == 'unassigned':
                        locked.crm_status = 'open'
                    locked.save(update_fields=['reply_mode', 'assigned_agent', 'crm_status'])
                caption = data.validated_data.get('caption', '').strip()
                message, _ = queue_message(self.business.id, item.id, client_id, caption or f'📎 {values["filename"]}',
                                           sender_type=self.member.role)
                MessageAttachment.objects.create(business=self.business, conversation=item, message=message,
                                                 uploaded_by=request.user, **values)
                AuditEvent.objects.create(business=self.business, actor=request.user, action='attachment.sent', object_id=str(message.id))
        except Exception:
            storage(values['storage_backend']).delete(values['storage_key'])  # no orphaned private files
            raise
        message = Message.objects.select_related('attachment__uploaded_by').get(pk=message.pk)
        return Response(MessageSerializer(message).data, status=201)

    @action(detail=True, methods=['get'], url_path=r'attachments/(?P<attachment_id>[0-9a-f-]{36})/download')
    def download_attachment(self, request, attachment_id=None, **kwargs):
        item = self.get_object()
        attachment = get_object_or_404(MessageAttachment, id=attachment_id, business=self.business, conversation=item)
        inline = request.query_params.get('inline') == '1' and attachment.kind in ('image', 'pdf')
        if not inline:
            AuditEvent.objects.create(business=self.business, actor=request.user, action='attachment.downloaded', object_id=str(attachment.id))
        return attachment_response(attachment, inline)

    @action(detail=True, methods=['post'], url_path='resolve-send')
    @transaction.atomic
    def resolve_send(self, request, **kwargs):
        self.require_admin()
        conversation = self.get_object()
        class Input(serializers.Serializer):
            message_id = serializers.UUIDField()
            outcome = serializers.ChoiceField(choices=['confirmed_sent', 'confirmed_not_sent'])
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        item = get_object_or_404(Message.objects.select_for_update(), id=data.validated_data['message_id'], business=self.business, conversation=conversation, send_state='unknown')
        item.send_state = 'submitted' if data.validated_data['outcome'] == 'confirmed_sent' else 'failed'
        item.last_error = 'Manually reconciled by an administrator.'
        item.save(update_fields=['send_state', 'last_error'])
        AuditEvent.objects.create(business=self.business, actor=request.user, action=f'message.{data.validated_data["outcome"]}', object_id=str(item.id))
        publish(self.business.id, 'message.updated', conversation.id)
        from .tasks import dispatch_conversation
        transaction.on_commit(lambda: enqueue(dispatch_conversation, self.business.id, conversation.id))
        return Response(MessageSerializer(item).data)

    @action(detail=True, methods=['post'], url_path='approve-draft')
    @transaction.atomic
    def approve_draft(self, request, **kwargs):
        from apps.ai_engine.models import ReplyJob
        conversation = Conversation.objects.select_for_update().get(
            id=self.get_object().id, business=self.business,
        )
        if conversation.reply_mode != 'assisted' or conversation.status != 'open':
            raise serializers.ValidationError('This conversation has no approvable AI draft in its current mode.')
        if conversation.connection.channel == 'whatsapp' and not conversation.connection.whatsapp_ready:
            raise serializers.ValidationError('WhatsApp is not connected.')
        job = ReplyJob.objects.select_for_update().filter(
            message__conversation=conversation, message__business_id=self.business.id, state='draft',
        ).order_by('-finished_at', '-message_id').first()
        if not job or not job.draft_content:
            raise serializers.ValidationError('There is no AI draft waiting for approval.')
        message, _ = queue_message(
            self.business.id, conversation.id, UUID(str(job.message_id)), job.draft_content, automated=True,
        )
        job.state = 'answered'
        job.save(update_fields=['state'])
        AuditEvent.objects.create(business=self.business, actor=request.user, action='ai.draft_approved', object_id=str(message.id))
        publish(self.business.id, 'conversation.updated', conversation.id)
        return Response(MessageSerializer(message).data, status=201)

    @action(detail=True, methods=['post'], url_path='reset-chatbot-menu')
    @transaction.atomic
    def reset_chatbot_menu(self, request, **kwargs):
        self.require_admin()
        item = Conversation.objects.select_for_update().get(pk=self.get_object().id, business=self.business)
        item.chatbot_state = {'state': 'MAIN_MENU'}
        item.save(update_fields=['chatbot_state'])
        publish(self.business.id, 'conversation.updated', item.id)
        return Response({'chatbot_state': item.chatbot_state})


class DashboardView(WorkspaceMixin, APIView):
    def get(self, request, **kwargs):
        whatsapp = self.business.conversations.filter(connection__provider='wppconnect')
        return Response({
            'connected_accounts': self.business.connections.exclude(provider=Connection.WEBSITE_PROVIDER).filter(state='connected', removed_at__isnull=True).count(),
            'connections': self.business.connections.exclude(provider=Connection.WEBSITE_PROVIDER).filter(removed_at__isnull=True).count(),
            'contacts': self.business.contacts.count(),
            'website_conversations': self.business.conversations.filter(connection__provider=Connection.WEBSITE_PROVIDER).count(),
            'whatsapp_conversations': whatsapp.count(),
            'simulation_conversations': self.business.conversations.filter(connection__provider=Connection.SIMULATION_PROVIDER).count() if settings.DEBUG else 0,
            'whatsapp_open_conversations': whatsapp.filter(status='open').count(),
            'whatsapp_unread_conversations': whatsapp.filter(unread_count__gt=0).count(),
            'whatsapp_connected': settings.WPPCONNECT_ENABLED and self.business.connections.filter(
                provider='wppconnect', state='connected', desired_state='open', removed_at__isnull=True,
            ).exclude(token_encrypted='').exists(),
            'open_conversations': self.business.conversations.filter(status='open').count(),
            'unread_conversations': self.business.conversations.filter(unread_count__gt=0).count(),
            'pending_events': WebhookEvent.objects.filter(business=self.business, processed_at__isnull=True).count(),
            'unknown_sends': Message.objects.filter(business=self.business, send_state='unknown').count(),
            **self.crm_counts(request),
        })

    def crm_counts(self, request):
        from apps.products.models import ProductLead
        from .followups import today_for
        from .models import FollowUp
        today = today_for(request)
        followups = FollowUp.objects.filter(business=self.business, status='pending')
        visible = Conversation.objects.filter(business=self.business, status='open')
        if not settings.DEBUG:
            visible = visible.exclude(connection__provider=Connection.SIMULATION_PROVIDER)
        return {
            'unassigned_conversations': visible.filter(assigned_agent__isnull=True).count(),
            'my_unread_conversations': visible.filter(assigned_agent=request.user, unread_count__gt=0).count(),
            'followups_due_today': followups.filter(due_date=today).count(),
            'followups_overdue': followups.filter(due_date__lt=today).count(),
            'new_leads': ProductLead.objects.filter(business=self.business, status__in=('new', 'converted'), deleted_at__isnull=True).count(),
        }


from .models import WebhookEvent
