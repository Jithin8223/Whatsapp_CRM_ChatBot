from django.db import models

from apps.conversations.models import Message
from apps.tenants.models import Business


def default_whatsapp_menu():
    return [
        {'label': 'Our services', 'question': 'What services do you offer?'},
        {'label': 'Pricing and quotation', 'question': 'How can I request a quotation?'},
        {'label': 'Business information', 'question': 'What are your business hours?'},
        {'label': 'Speak to a human agent', 'question': ''},
    ]


def default_chatbot_menu():
    return [
        {'label': 'View Products', 'action': 'products'},
        {'label': 'Product Prices', 'action': 'prices'},
        {'label': 'Product Features', 'action': 'features'},
        {'label': 'Request Quotation', 'action': 'quotation'},
        {'label': 'Business Information', 'action': 'business_info'},
        {'label': 'Other Questions', 'action': 'general_question'},
    ]


class AISettings(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    provider = models.CharField(max_length=12, choices=[('knowledge', 'Knowledge only'), ('openai', 'OpenAI')], default='knowledge')
    instructions = models.TextField(blank=True, max_length=3000)
    issue_intake = models.BooleanField(default=True)
    lead_intake = models.BooleanField(default=False)
    retention_days = models.PositiveSmallIntegerField(null=True, blank=True)
    whatsapp_greeting = models.CharField(max_length=480, default='Hello! Welcome to our customer support.')
    whatsapp_menu = models.JSONField(default=default_whatsapp_menu, blank=True)
    whatsapp_handoff_terms = models.JSONField(default=list, blank=True)
    chatbot_greeting = models.CharField(max_length=480, default='Welcome to our business! \U0001F44B')
    chatbot_menu = models.JSONField(default=default_chatbot_menu, blank=True)


class KnowledgeEntry(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    question = models.CharField(max_length=240)
    answer = models.TextField(max_length=3000)
    category = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)


class ReplyJob(models.Model):
    message = models.OneToOneField(Message, primary_key=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=12, default='queued', db_index=True)
    draft_content = models.TextField(blank=True, max_length=4096)
    # Sanitized reason for skipped/failed jobs (never customer content or credentials).
    error = models.CharField(max_length=160, blank=True, default='')
    # menu | knowledge | generative | intake | fallback | handoff ('' before tracking existed).
    source = models.CharField(max_length=12, blank=True, default='')
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    latency_ms = models.PositiveIntegerField(null=True)
