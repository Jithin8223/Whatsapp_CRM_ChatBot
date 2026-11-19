import uuid
from django.conf import settings
from django.db import models
from apps.tenants.models import Business


def session_name():
    return f'crm_{uuid.uuid4().hex}'


class Connection(models.Model):
    WEBSITE_PROVIDER = 'website'
    SIMULATION_PROVIDER = 'whatsapp_simulation'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='connections')
    name = models.CharField(max_length=100)
    provider = models.CharField(max_length=20, default='wppconnect')
    session = models.CharField(max_length=64, unique=True, default=session_name, editable=False)
    token_encrypted = models.TextField(blank=True)
    state = models.CharField(max_length=24, default='new')
    desired_state = models.CharField(max_length=20, default='closed')
    ai_enabled = models.BooleanField(default=False)
    qr_encrypted = models.TextField(blank=True)
    qr_expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=160, blank=True)
    # Human-readable pairing progress (from WPPConnect status events); never QR or token data.
    status_detail = models.CharField(max_length=200, blank=True, default='')
    state_changed_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    @property
    def channel(self):
        return 'website' if self.provider == self.WEBSITE_PROVIDER else 'whatsapp'

    @property
    def whatsapp_ready(self):
        return bool(
            settings.WPPCONNECT_ENABLED and self.provider == 'wppconnect' and self.token_encrypted
            and self.state == 'connected' and self.desired_state == 'open' and not self.removed_at
        )

    @property
    def supports_auto_reply(self):
        return self.channel == 'website' or (self.simulated and settings.DEBUG) or (self.ai_enabled and self.whatsapp_ready)

    @property
    def simulated(self):
        return self.provider == self.SIMULATION_PROVIDER

    class Meta:
        indexes = [models.Index(fields=['business', 'created_at'])]


class ConnectionOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    action = models.CharField(max_length=20)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
