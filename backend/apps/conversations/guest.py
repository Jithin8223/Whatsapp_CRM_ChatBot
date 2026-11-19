import hashlib
import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit, urlencode

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.tenants.models import Business
from apps.tenants.permissions import WorkspaceMixin
from apps.ai_engine.models import AISettings, ReplyJob
from apps.whatsapp.models import Connection
from .models import Contact, Conversation, GuestSession, WebsiteWidget, WebsiteGuestCounter
from .services import record_inbound, cancel_ai_replies
from .mobile import mobile_cipher, email_cipher
from apps.notifications.events import publish

DEFAULT_SUGGESTIONS = (
    'What services do you offer?', 'How can I contact support?',
    'What are your working hours?', 'Can I speak with a human agent?',
)


def normalize_whatsapp_number(value):
    if not re.fullmatch(r'\+?[0-9 ()-]+', value.strip()):
        return None
    digits = re.sub(r'[^0-9]', '', value)
    return digits if re.fullmatch(r'[1-9][0-9]{6,14}', digits) else None


def whatsapp_contact_url(widget):
    number = normalize_whatsapp_number(widget.whatsapp_number)
    if not number:
        return None
    url = f'https://wa.me/{number}'
    return url + '?' + urlencode({'text': widget.whatsapp_message}) if widget.whatsapp_message else url

def valid_origin(origin):
    try:
        url = urlsplit(origin)
        return (
            url.hostname and origin == f'{url.scheme}://{url.netloc}' and not url.username
            and not url.password and (url.port is None or url.port > 0)
            and (url.scheme == 'https' or (
                settings.DEBUG and url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1')
            ))
        )
    except ValueError:
        return False


class WidgetSerializer(serializers.ModelSerializer):
    origins = serializers.ListField(child=serializers.CharField(max_length=255), max_length=20)
    color = serializers.RegexField(r'^#[0-9a-fA-F]{6}$')
    suggestions = serializers.ListField(child=serializers.CharField(max_length=120), max_length=6, required=False)
    whatsapp_number = serializers.CharField(max_length=40, allow_blank=True, required=False)

    class Meta:
        model = WebsiteWidget
        fields = ['public_key', 'enabled', 'title', 'welcome', 'color', 'origins', 'suggestions', 'whatsapp_number', 'whatsapp_message']
        read_only_fields = ['public_key']

    def validate_origins(self, values):
        invalid = [origin for origin in values if not valid_origin(origin)]
        if invalid:
            local = ' In development, http://localhost:PORT and http://127.0.0.1:PORT are also allowed.' if settings.DEBUG else ''
            raise serializers.ValidationError(
                f'Invalid origin “{invalid[0][:80]}”. Use the exact origin such as https://www.example.com, '
                f'without a path or trailing slash.{local}')
        return list(dict.fromkeys(values))

    def validate_whatsapp_number(self, value):
        if not value:
            return ''
        number = normalize_whatsapp_number(value)
        if not number:
            raise serializers.ValidationError('Enter the international number with its country code, for example + followed by digits.')
        return number

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['suggestions'] = data['suggestions'] or list(DEFAULT_SUGGESTIONS)
        return data

    def validate(self, values):
        public_enabled = values.get('enabled', self.instance.enabled) or values.get('whatsapp_number', self.instance.whatsapp_number)
        if public_enabled and not values.get('origins', self.instance.origins):
            raise serializers.ValidationError({'origins': 'Add an allowed website origin before publishing, for example http://127.0.0.1:5500 in local development.'})
        return values


class WidgetSettings(WorkspaceMixin, APIView):
    def get(self, request, **kwargs):
        self.require_admin()
        widget = WebsiteWidget.objects.filter(business=self.business).first()
        return Response(WidgetSerializer(widget).data if widget else None)

    @transaction.atomic
    def patch(self, request, **kwargs):
        self.require_admin()
        Business.objects.select_for_update().get(pk=self.business.pk)
        widget = WebsiteWidget.objects.filter(business=self.business).first()
        if not widget:
            endpoint = Connection.objects.create(
                business=self.business, name='Website chat', provider=Connection.WEBSITE_PROVIDER,
                state='connected', desired_state='open',
            )
            widget = WebsiteWidget.objects.create(business=self.business, connection=endpoint)
        data = WidgetSerializer(widget, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        return Response(data.data)


def guest_session(key, token, origin):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token or '') or not valid_origin(origin):
        return None
    return GuestSession.objects.select_related('widget', 'conversation').filter(
        digest=hashlib.sha256(token.encode()).hexdigest(), origin=origin, expires_at__gt=timezone.now(),
        widget__public_key=key, widget__enabled=True,
        widget__connection__provider=Connection.WEBSITE_PROVIDER, widget__connection__removed_at__isnull=True,
        conversation__connection_id=F('widget__connection_id'), conversation__business_id=F('widget__business_id'),
        conversation__contact__business_id=F('widget__business_id'),
        conversation__contact__website_visitor_id__isnull=False,
    ).first()


class GuestThrottle(AnonRateThrottle):
    scope = 'website_guest'
    rate = '120/min'


class GuestCreateThrottle(AnonRateThrottle):
    scope = 'website_guest_create'
    rate = '15/min'


class GuestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [GuestThrottle]
    requires_published_widget = True

    def dispatch(self, request, *args, **kwargs):
        self.origin = request.headers.get('Origin', '')
        if not self.origin and request.headers.get('Sec-Fetch-Site') == 'same-origin':
            try:
                referer = urlsplit(request.headers.get('Referer', ''))
                self.origin = f'{referer.scheme}://{referer.netloc}'
            except ValueError:
                return HttpResponse(status=403)
        widgets = WebsiteWidget.objects.filter(
            public_key=kwargs['key'], connection__provider=Connection.WEBSITE_PROVIDER,
            connection__removed_at__isnull=True, connection__business_id=F('business_id'),
        )
        if self.requires_published_widget:
            widgets = widgets.filter(enabled=True)
        self.widget = widgets.first()
        if not self.widget or not valid_origin(self.origin) or self.origin not in self.widget.origins:
            return HttpResponse(status=403)
        response = super().dispatch(request, *args, **kwargs)
        response['Access-Control-Allow-Origin'] = self.origin
        response['Access-Control-Expose-Headers'] = 'Retry-After'
        patch_vary_headers(response, ['Origin'])
        return response

    def options(self, request, **kwargs):
        response = Response(status=204)
        response['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PATCH, OPTIONS'
        return response

    def session(self, request):
        scheme, _, token = request.headers.get('Authorization', '').partition(' ')
        if scheme.lower() != 'bearer':
            raise NotAuthenticated('Chat session expired. Start a new chat.')
        session = guest_session(self.widget.public_key, token, self.origin)
        if not session:
            raise NotAuthenticated('Chat session expired. Start a new chat.')
        return session


class GuestConfig(GuestView):
    def get(self, request, **kwargs):
        widget = self.widget
        config = AISettings.objects.filter(business_id=widget.business_id, enabled=True).first()
        menu = []
        if config:
            from apps.ai_engine.menu import menu_items
            menu = [item['label'] for item in menu_items(config)]
        return Response({
            'title': widget.title, 'welcome': widget.welcome, 'color': widget.color, 'whatsapp': whatsapp_contact_url(widget) or '',
            'external_ai': bool(config and config.provider == 'openai'),
            'automatic': bool(config),
            # Same numbered menu as WhatsApp; a tapped option sends its number.
            'menu': menu,
            'suggestions': widget.suggestions or ([] if menu else list(DEFAULT_SUGGESTIONS)),
        })

    def get_throttles(self):
        return [GuestCreateThrottle()] if self.request.method == 'POST' else super().get_throttles()

    @transaction.atomic
    def post(self, request, **kwargs):
        widget = self.widget
        Business.objects.select_for_update().get(pk=widget.business_id)
        counter, _ = WebsiteGuestCounter.objects.get_or_create(business_id=widget.business_id)
        counter.last_number += 1
        counter.save(update_fields=['last_number'])
        contact = Contact.objects.create(
            business_id=widget.business_id, website_visitor_id=uuid.uuid4(), guest_number=counter.last_number,
        )
        conversation = Conversation.objects.create(
            business=widget.business, connection=widget.connection, contact=contact,
            reply_mode='ai' if AISettings.objects.filter(business=widget.business, enabled=True).exists() else 'waiting',
        )
        token = secrets.token_urlsafe(32)
        GuestSession.objects.create(
            widget=widget, conversation=conversation, origin=self.origin,
            digest=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now() + timedelta(days=1),
        )
        return Response({'token': token}, status=201)


class PublicWhatsAppLink(GuestView):
    requires_published_widget = False
    http_method_names = ['get', 'head', 'options']

    def get(self, request, **kwargs):
        url = whatsapp_contact_url(self.widget)
        return Response({'url': url, 'detail': None if url else 'WhatsApp contact is unavailable because a valid business number has not been configured.'})


class GuestProfile(GuestView):
    def patch(self, request, **kwargs):
        session = self.session(request)

        class Input(serializers.Serializer):
            display_name = serializers.CharField(max_length=120, allow_blank=True, required=False)
            mobile = serializers.CharField(max_length=40, allow_blank=True, required=False)
            email = serializers.EmailField(max_length=254, allow_blank=True, required=False)

            def validate_mobile(self, value):
                normalized = re.sub(r'[ ()-]', '', value)
                if value and not re.fullmatch(r'\+[1-9][0-9]{7,14}', normalized):
                    raise serializers.ValidationError('Use an international number starting with + and its country code.')
                return normalized

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        contact = session.conversation.contact
        fields = []
        if 'display_name' in data.validated_data:
            contact.display_name = data.validated_data['display_name']
            fields.append('display_name')
        if 'mobile' in data.validated_data:
            mobile = data.validated_data['mobile']
            contact.mobile_encrypted = mobile_cipher().encrypt(mobile.encode()).decode() if mobile else ''
            fields.append('mobile_encrypted')
        if 'email' in data.validated_data:
            email = data.validated_data['email'].strip().lower()
            contact.email_encrypted = email_cipher().encrypt(email.encode()).decode() if email else ''
            fields.append('email_encrypted')
        if fields:
            contact.save(update_fields=[*fields, 'updated_at'])
            publish(session.widget.business_id, 'conversation.updated', session.conversation_id)
        return Response({'display_name': contact.display_name, 'mobile_shared': bool(contact.mobile_encrypted), 'email_shared': bool(contact.email_encrypted)})


class GuestMessages(GuestView):
    def get(self, request, **kwargs):
        session = self.session(request)
        rows = session.conversation.messages.filter(business_id=session.widget.business_id).select_related('attachment').order_by('-created_at', '-id')[:100]
        return Response({'messages': [
            {
                'direction': item.direction, 'content': item.content, 'created_at': item.created_at,
                'attachment': guest_attachment(item),
                # Outbound only: lets the widget confirm delivery / reading of team and bot replies.
                'id': str(item.id) if item.direction == 'outbound' else None,
                'status': item.delivery_status if item.direction == 'outbound' else None,
                'request_id': item.provider_id.rsplit(':', 1)[-1] if item.direction == 'inbound' and (item.provider_id or '').startswith('guest:') else None,
            } for item in reversed(list(rows))
        ], 'display_name': session.conversation.contact.display_name,
            'mobile_shared': bool(session.conversation.contact.mobile_encrypted),
            'email_shared': bool(session.conversation.contact.email_encrypted),
            'guest_label': session.conversation.contact.guest_label,
            'mode': session.conversation.reply_mode if (
            session.conversation.reply_mode != 'ai'
            or AISettings.objects.filter(business_id=session.widget.business_id, enabled=True).exists()
        ) else 'waiting', 'pending': ReplyJob.objects.filter(
            message__conversation=session.conversation, state__in=('queued', 'running'),
        ).exists()})

    def post(self, request, **kwargs):
        session = self.session(request)

        class Input(serializers.Serializer):
            id = serializers.UUIDField()
            content = serializers.CharField(max_length=2000)

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        conversation = session.conversation
        _, created = record_inbound(
            session.widget.business_id, conversation.connection_id, conversation.contact_id,
            f'guest:{conversation.id}:{data.validated_data["id"]}', data.validated_data['content'],
        )
        if created:
            from apps.products.models import ProductLead
            from apps.products.retrieval import identify_products
            products, ambiguous = identify_products(session.widget.business_id, data.validated_data['content'])
            # Record product interest only when a specific product was named; greetings
            # and menu numbers are not enquiries. Quotations are saved by the menu flow.
            if len(products) == 1 and not ambiguous:
                _, lead_created = ProductLead.objects.get_or_create(
                    conversation=conversation,
                    defaults={
                        'business_id': session.widget.business_id, 'product': products[0],
                        'summary': data.validated_data['content'][:500],
                    },
                )
                if lead_created:
                    publish(session.widget.business_id, 'lead.created', conversation.id)
        return Response(status=201 if created else 200)


def guest_attachment(message):
    item = getattr(message, 'attachment', None)
    if not item:
        return None
    return {'id': str(item.id), 'filename': item.filename, 'content_type': item.content_type, 'kind': item.kind, 'size': item.size}


class GuestAttachment(GuestView):
    """A visitor may download files sent in their own conversation only (Bearer guest token)."""
    http_method_names = ['get', 'options']

    def get(self, request, attachment_id, **kwargs):
        from .models import MessageAttachment
        from .views import attachment_response
        session = self.session(request)
        attachment = get_object_or_404(MessageAttachment, id=attachment_id, conversation_id=session.conversation_id,
                                       business_id=session.widget.business_id)
        return attachment_response(attachment, inline=request.query_params.get('inline') == '1' and attachment.kind in ('image', 'pdf'),
                                   cross_origin=True)


class GuestReceipts(GuestView):
    """Website chat receipts sent by widget.js for messages in the visitor's own conversation:
    'delivered' when the widget received a reply, 'read' only when the bubble was visible in an
    open chat on a visible page. Never inferred from a socket merely being connected."""

    @transaction.atomic
    def post(self, request, **kwargs):
        from .models import Message
        from .services import apply_client_receipt
        session = self.session(request)

        class Input(serializers.Serializer):
            delivered = serializers.ListField(child=serializers.UUIDField(), max_length=100, required=False, default=list)
            read = serializers.ListField(child=serializers.UUIDField(), max_length=100, required=False, default=list)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        wanted = {**{key: 'delivered' for key in data.validated_data['delivered']}, **{key: 'read' for key in data.validated_data['read']}}
        changed = 0
        for message in Message.objects.select_for_update().filter(
            id__in=list(wanted), conversation_id=session.conversation_id, business_id=session.widget.business_id,
            direction='outbound', connection__provider=Connection.WEBSITE_PROVIDER,
        ):
            changed += apply_client_receipt(message, wanted[message.id])
        if changed:
            publish(session.widget.business_id, 'message.updated', session.conversation_id)
        return Response({'updated': changed})


class GuestHandoff(GuestView):
    @transaction.atomic
    def post(self, request, **kwargs):
        session = self.session(request)
        item = Conversation.objects.select_for_update().get(pk=session.conversation_id, business_id=session.widget.business_id)
        if item.reply_mode == 'ai' or item.status == 'closed':
            cancel_ai_replies(item)
            item.reply_mode = 'waiting'
            item.handoff_at = timezone.now()
            item.status = 'open'
            item.assigned_agent_id = None
            item.unread_count += 1
            item.last_activity_at = timezone.now()
            item.save(update_fields=['reply_mode', 'handoff_at', 'status', 'assigned_agent', 'unread_count', 'last_activity_at'])
            publish(item.business_id, 'conversation.updated', item.id)
        return Response(status=204)
