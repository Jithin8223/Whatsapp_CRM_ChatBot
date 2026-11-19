from rest_framework import serializers
from .models import Contact, Conversation, Message
from apps.ai_engine.models import AISettings, ReplyJob


class ContactSerializer(serializers.ModelSerializer):
    whatsapp_id = serializers.CharField(max_length=100, allow_null=False)
    guest_label = serializers.CharField(read_only=True)
    phone = serializers.CharField(source='crm_phone', read_only=True)
    email = serializers.CharField(source='crm_email', read_only=True)

    class Meta:
        model = Contact
        fields = ['id', 'whatsapp_id', 'display_name', 'guest_label', 'phone', 'email', 'created_at', 'updated_at']
        read_only_fields = ['id', 'phone', 'created_at', 'updated_at']

    def validate_whatsapp_id(self, value):
        import re
        if not re.fullmatch(r'[0-9]{5,25}@(c\.us|lid)', value):
            raise serializers.ValidationError('Use a direct WhatsApp identifier, such as 919876543210@c.us.')
        return value


def attachment_data(item):
    """Public metadata only: never the storage key or a storage URL."""
    return {'id': str(item.id), 'filename': item.filename, 'content_type': item.content_type, 'kind': item.kind,
            'size': item.size, 'created_at': item.created_at, 'message_id': str(item.message_id),
            'uploaded_by': item.uploaded_by.username if item.uploaded_by_id and item.uploaded_by else None}


class MessageSerializer(serializers.ModelSerializer):
    # For automatic replies: menu | knowledge | generative | intake | fallback | handoff.
    reply_source = serializers.SerializerMethodField()
    attachment = serializers.SerializerMethodField()

    def get_attachment(self, obj):
        item = getattr(obj, 'attachment', None) if hasattr(obj, 'attachment') else None
        return attachment_data(item) if item else None

    def get_reply_source(self, obj):
        if not obj.automated or not obj.client_id:
            return ''
        return self.context.get('reply_sources', {}).get(obj.client_id, '')

    class Meta:
        model = Message
        fields = ['id', 'conversation', 'client_id', 'provider_id', 'direction', 'automated', 'sender_type', 'content', 'send_state', 'delivery_status', 'provider_timestamp', 'created_at', 'last_error', 'reply_source', 'attachment', 'delivered_at', 'read_at', 'seen_at']


class ConversationSerializer(serializers.ModelSerializer):
    contact = ContactSerializer(read_only=True)
    last_message = MessageSerializer(read_only=True)
    connection_name = serializers.CharField(source='connection.name', read_only=True)
    channel = serializers.CharField(source='connection.channel', read_only=True)
    whatsapp_connected = serializers.BooleanField(source='connection.whatsapp_ready', read_only=True)
    automation_available = serializers.SerializerMethodField()
    automation_blocker = serializers.SerializerMethodField()
    simulated = serializers.BooleanField(source='connection.simulated', read_only=True)
    ai_pending = serializers.BooleanField(read_only=True, default=False)
    ai_draft = serializers.SerializerMethodField()

    def _workspace_ai(self, obj):
        cache = self.context.setdefault('_workspace_ai', {})
        if obj.business_id not in cache:
            cache[obj.business_id] = AISettings.objects.filter(business_id=obj.business_id, enabled=True).exists()
        return cache[obj.business_id]

    def get_automation_blocker(self, obj):
        """Human-readable reason AI modes are unavailable, matching partial_update validation."""
        connection = obj.connection
        if connection.channel == 'whatsapp' and not connection.simulated:
            if not connection.whatsapp_ready:
                return 'WhatsApp is not connected.'
            if not connection.ai_enabled:
                return 'AI replies are off for this WhatsApp connection. Enable them on the Connections page.'
        elif not connection.supports_auto_reply:
            return 'Automatic replies are unavailable for this channel.'
        if not self._workspace_ai(obj):
            return 'Automatic replies are disabled in AI settings for this workspace.'
        return ''

    def get_automation_available(self, obj):
        return not self.get_automation_blocker(obj)

    lead = serializers.SerializerMethodField()
    reply_failure = serializers.SerializerMethodField()
    assigned_agent_name = serializers.CharField(source='assigned_agent.username', read_only=True, default=None)
    followups = serializers.SerializerMethodField()
    # Annotated by ConversationViewSet.get_queryset (no per-row queries).
    last_viewed_by_name = serializers.CharField(read_only=True, default=None)
    last_viewed_at = serializers.DateTimeField(read_only=True, default=None)
    pending_followups = serializers.IntegerField(read_only=True, default=0)
    next_followup_date = serializers.DateField(read_only=True, default=None)

    def get_followups(self, obj):
        pending = obj.followups.filter(business_id=obj.business_id, status='pending')
        upcoming = pending.order_by('due_date', 'due_time').values_list('due_date', flat=True).first()
        return {'pending': pending.count(), 'total': obj.followups.filter(business_id=obj.business_id).count(), 'next_due': upcoming}

    def get_lead(self, obj):
        from apps.products.models import ProductLead
        lead = ProductLead.objects.filter(conversation=obj, business_id=obj.business_id, deleted_at__isnull=True).select_related('product').first()
        if not lead:
            return None
        return {'id': lead.id, 'product': lead.product.name if lead.product else None, 'summary': lead.summary,
                'status': lead.status, 'updated_at': lead.updated_at}

    def get_reply_failure(self, obj):
        """Most recent automatic reply that failed after the last successful one."""
        jobs = ReplyJob.objects.filter(message__conversation=obj, message__business_id=obj.business_id)
        failed = jobs.filter(state='failed').order_by('-finished_at').values('error', 'finished_at').first()
        if not failed:
            return None
        later = jobs.filter(state__in=('answered', 'handoff', 'draft'), finished_at__gt=failed['finished_at']).exists()
        return None if later else failed

    def get_ai_draft(self, obj):
        job = ReplyJob.objects.filter(message__conversation=obj, message__business_id=obj.business_id, state='draft').order_by('-finished_at', '-message_id').first()
        return {'content': job.draft_content, 'message_id': str(job.message_id)} if job and job.draft_content else None

    class Meta:
        model = Conversation
        fields = ['id', 'connection', 'connection_name', 'channel', 'whatsapp_connected', 'automation_available', 'automation_blocker', 'simulated', 'contact', 'status', 'reply_mode', 'assigned_agent', 'last_message', 'last_activity_at', 'unread_count', 'created_at', 'internal_note', 'ai_pending', 'ai_draft', 'chatbot_state', 'handoff_at', 'lead', 'reply_failure',
                  'crm_status', 'assigned_agent_name', 'followups', 'last_viewed_by_name', 'last_viewed_at',
                  'pending_followups', 'next_followup_date']
        read_only_fields = ['chatbot_state', 'handoff_at', 'crm_status']

    def get_fields(self):
        fields = super().get_fields()
        if getattr(self.context.get('view'), 'action', None) == 'list':
            for name in ('internal_note', 'ai_pending', 'ai_draft', 'lead', 'reply_failure', 'followups'):
                fields.pop(name)
        return fields
