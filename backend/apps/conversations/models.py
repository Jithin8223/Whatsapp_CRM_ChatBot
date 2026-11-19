import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.tenants.models import Business
from apps.whatsapp.models import Connection


class Contact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='contacts')
    whatsapp_id = models.CharField(max_length=100, null=True, blank=True)
    website_visitor_id = models.UUIDField(null=True, blank=True)
    guest_number = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    mobile_encrypted = models.TextField(blank=True)
    email_encrypted = models.TextField(blank=True)
    display_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['business', 'whatsapp_id'], name='unique_business_contact'),
            models.UniqueConstraint(fields=['business', 'website_visitor_id'], name='unique_business_website_visitor'),
            models.UniqueConstraint(fields=['business', 'guest_number'], name='unique_business_guest_number'),
            models.CheckConstraint(
                condition=(
                    models.Q(whatsapp_id__isnull=False, website_visitor_id__isnull=True)
                    | models.Q(whatsapp_id__isnull=True, website_visitor_id__isnull=False)
                ),
                name='contact_has_one_channel_address',
            ),
        ]
        indexes = [models.Index(fields=['business', 'display_name'])]

    @property
    def guest_label(self):
        return f'Guest #{self.guest_number:04d}' if self.guest_number is not None else None

    @property
    def crm_phone(self):
        if not self.mobile_encrypted:
            return self.phone if self.website_visitor_id is None else ''
        from .mobile import mobile_cipher
        return mobile_cipher().decrypt(self.mobile_encrypted.encode()).decode()

    @property
    def crm_email(self):
        if not self.email_encrypted:
            return ''
        from .mobile import email_cipher
        return email_cipher().decrypt(self.email_encrypted.encode()).decode()


class WebsiteGuestCounter(models.Model):
    business = models.OneToOneField(Business, primary_key=True, on_delete=models.CASCADE)
    last_number = models.PositiveBigIntegerField(default=0)


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='conversations')
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT, related_name='conversations')
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name='conversations')
    status = models.CharField(max_length=10, default='open', choices=[('open', 'Open'), ('closed', 'Closed')])
    reply_mode = models.CharField(max_length=8, default='manual', choices=[
        ('manual', 'Human active'), ('ai', 'AI active'), ('assisted', 'AI draft'), ('waiting', 'Waiting for agent'),
    ])
    chatbot_state = models.JSONField(default=dict, blank=True)
    # Set when the customer or the bot explicitly escalated to a human; cleared on return to AI.
    handoff_at = models.DateTimeField(null=True, blank=True)
    # Sales/CRM pipeline status, deliberately separate from `status` (open/resolved),
    # `assigned_agent` (owner) and `reply_mode` (AI vs human replies).
    CRM_STATUSES = [
        ('unassigned', 'Unassigned'), ('open', 'Open'), ('unanswered', 'Unanswered'),
        ('not_interested', 'Not Interested'), ('converted', 'Converted'),
    ]
    crm_status = models.CharField(max_length=16, choices=CRM_STATUSES, default='unassigned')
    internal_note = models.TextField(blank=True, max_length=4000)
    assigned_agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    last_message = models.ForeignKey('Message', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    last_activity_at = models.DateTimeField(default=timezone.now)
    unread_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['connection', 'contact'], name='unique_connection_conversation')]
        indexes = [
            models.Index(fields=['business', '-last_activity_at']), models.Index(fields=['business', 'status']),
            models.Index(fields=['business', 'crm_status']), models.Index(fields=['business', 'assigned_agent']),
        ]


class ConversationView(models.Model):
    """Who opened a conversation and when. One row per member; repeated opens within
    VIEW_DEDUP_WINDOW (UI refreshes) do not count again. Never changes ownership."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    first_viewed_at = models.DateTimeField(auto_now_add=True)
    last_viewed_at = models.DateTimeField(default=timezone.now)
    view_count = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['conversation', 'user'], name='unique_conversation_viewer')]
        indexes = [models.Index(fields=['business', 'conversation', '-last_viewed_at'])]


class FollowUp(models.Model):
    ACTIONS = [
        ('call', 'Call the customer'), ('send_quotation', 'Send quotation'),
        ('share_information', 'Share product information'), ('schedule_meeting', 'Schedule a meeting'),
        ('decision', 'Follow up on a decision'), ('other', 'Other'),
    ]
    STATUSES = [('pending', 'Pending'), ('done', 'Done')]
    PRIORITIES = [('', 'None'), ('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='followups')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='followups')
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='followups')
    lead = models.ForeignKey('products.ProductLead', null=True, blank=True, on_delete=models.SET_NULL, related_name='followups')
    customer_said = models.TextField(max_length=4000, blank=True)
    team_said = models.TextField(max_length=4000, blank=True)
    next_action = models.CharField(max_length=20, choices=ACTIONS, default='call')
    next_steps = models.TextField(max_length=4000, blank=True)
    due_date = models.DateField()
    due_time = models.TimeField(null=True, blank=True)
    assigned_agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='followups')
    status = models.CharField(max_length=8, choices=STATUSES, default='pending')
    priority = models.CharField(max_length=8, choices=PRIORITIES, blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['business', 'status', 'due_date']),
            models.Index(fields=['business', 'assigned_agent', 'due_date']),
            models.Index(fields=['business', 'conversation']),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(status='pending', completed_at__isnull=True) | models.Q(status='done', completed_at__isnull=False),
                                   name='followup_completion_consistent'),
        ]


class WebsiteWidget(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name='widget')
    connection = models.OneToOneField(Connection, on_delete=models.PROTECT)
    public_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    enabled = models.BooleanField(default=False)
    title = models.CharField(max_length=80, default='Support')
    welcome = models.CharField(max_length=240, default='How can we help?')
    color = models.CharField(max_length=7, default='#123b30')
    origins = models.JSONField(default=list)
    suggestions = models.JSONField(default=list, blank=True)
    whatsapp_number = models.CharField(max_length=15, blank=True)
    whatsapp_message = models.CharField(max_length=240, blank=True)


class GuestSession(models.Model):
    widget = models.ForeignKey(WebsiteWidget, on_delete=models.CASCADE)
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    origin = models.CharField(max_length=255)
    expires_at = models.DateTimeField(db_index=True)


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    connection = models.ForeignKey(Connection, on_delete=models.PROTECT)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    provider_id = models.CharField(max_length=255, null=True, blank=True)
    client_id = models.UUIDField(null=True, blank=True)
    direction = models.CharField(max_length=10, choices=[('inbound', 'Inbound'), ('outbound', 'Outbound')])
    automated = models.BooleanField(default=False)
    sender_type = models.CharField(max_length=10, choices=[('customer', 'Customer'), ('ai', 'AI assistant'), ('admin', 'Administrator'), ('agent', 'Agent')], default='agent')
    content = models.TextField()
    send_state = models.CharField(max_length=20, default='received')
    delivery_status = models.CharField(max_length=20, default='pending')
    provider_timestamp = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    sending_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=160, blank=True)
    # Outbound receipts: when the CRM received the first delivered/read confirmation from the
    # recipient's channel (WPPConnect acks carry no receipt time of their own).
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # Inbound: when a team member first viewed the message in the CRM (not a customer receipt).
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['connection', 'provider_id'], name='unique_provider_message'),
            models.UniqueConstraint(fields=['conversation', 'client_id'], name='unique_client_message'),
        ]
        indexes = [models.Index(fields=['business', 'conversation', 'created_at']), models.Index(fields=['send_state', 'next_attempt_at'])]


class MessageAttachment(models.Model):
    """A file sent by the team. Bytes live in private storage (local volume or a private
    Supabase Storage bucket) under an opaque key; they are only served to workspace members
    and to the website visitor who owns the conversation, never via a public URL."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='attachments')
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='attachment')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    storage_backend = models.CharField(max_length=16)
    storage_key = models.CharField(max_length=255, unique=True)
    filename = models.CharField(max_length=180)
    content_type = models.CharField(max_length=120)
    kind = models.CharField(max_length=12)  # image | pdf | document | spreadsheet | text
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['business', 'conversation', '-created_at'])]


class DeliveryEvent(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    provider_id = models.CharField(max_length=255)
    ack = models.SmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['connection', 'provider_id', 'ack'], name='unique_message_ack')]


class WebhookEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    key = models.CharField(max_length=64)
    payload_encrypted = models.TextField()
    attempts = models.PositiveSmallIntegerField(default=0)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['connection', 'key'], name='unique_webhook_event')]
        indexes = [models.Index(fields=['processed_at', 'created_at'])]
