from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.permissions import WorkspaceMixin
from apps.tenants.models import Business
from apps.conversations.models import Message
from .models import AISettings, KnowledgeEntry, ReplyJob


class SettingsSerializer(serializers.ModelSerializer):
    retention_days = serializers.IntegerField(min_value=1, max_value=3650, allow_null=True, required=False)
    whatsapp_handoff_terms = serializers.ListField(child=serializers.CharField(max_length=60), max_length=12, required=False)

    class MenuItem(serializers.Serializer):
        label = serializers.CharField(max_length=70)
        question = serializers.CharField(max_length=240, allow_blank=True)

    whatsapp_menu = MenuItem(many=True, max_length=9, required=False)
    chatbot_greeting = serializers.CharField(max_length=480, required=False)

    class ChatbotMenuItem(serializers.Serializer):
        label = serializers.CharField(max_length=70)
        action = serializers.ChoiceField(choices=['products', 'prices', 'features', 'quotation', 'business_info', 'general_question'])

    chatbot_menu = ChatbotMenuItem(many=True, min_length=2, max_length=9, required=False)

    class Meta:
        model = AISettings
        fields = ['enabled', 'provider', 'instructions', 'issue_intake', 'lead_intake', 'retention_days',
                  'whatsapp_greeting', 'whatsapp_menu', 'whatsapp_handoff_terms', 'chatbot_greeting', 'chatbot_menu']

    def validate(self, values):
        menu = values.get('chatbot_menu')
        if menu and len({item['action'] for item in menu}) != len(menu):
            raise serializers.ValidationError({'chatbot_menu': 'Each chatbot menu action can appear only once.'})
        if (values.get('enabled', self.instance.enabled) and values.get('provider', self.instance.provider) == 'openai'
                and not (settings.CRM_AI_API_KEY and settings.CRM_AI_MODEL)):
            raise serializers.ValidationError('The server administrator must configure the AI provider before enabling it.')
        return values


class AISettingsView(WorkspaceMixin, APIView):
    def payload(self, config):
        jobs = ReplyJob.objects.filter(message__business=self.business)
        errors = []
        if self.request.query_params.get('channel') == 'whatsapp':
            jobs = jobs.filter(message__connection__provider='wppconnect')
            errors = list(Message.objects.filter(
                business=self.business, connection__provider='wppconnect', direction='outbound',
                send_state__in=('failed', 'unknown', 'queued'),
            ).exclude(last_error='').order_by('-created_at').values('send_state', 'last_error', 'created_at')[:5])
            errors += [
                {'send_state': 'reply_failed', 'last_error': 'Automatic reply failed: ' + row['error'],
                 'created_at': row['finished_at']}
                for row in jobs.filter(state='failed').exclude(error='').order_by('-finished_at').values('error', 'finished_at')[:5]
            ]
            errors.sort(key=lambda row: row['created_at'].timestamp() if row['created_at'] else 0, reverse=True)
            errors = errors[:8]
        return {
            **SettingsSerializer(config).data,
            'external_available': bool(settings.CRM_AI_API_KEY and settings.CRM_AI_MODEL),
            'stats': jobs.filter(state__in=['answered', 'handoff', 'draft', 'failed'])
            .values('state').annotate(count=Count('state'), average_latency_ms=Avg('latency_ms')).order_by('state'),
            'recent_errors': errors,
        }

    def get(self, request, **kwargs):
        self.require_admin()
        config = AISettings.objects.filter(business=self.business).first() or AISettings(business=self.business)
        return Response(self.payload(config))

    def patch(self, request, **kwargs):
        self.require_admin()
        config, _ = AISettings.objects.get_or_create(business=self.business)
        data = SettingsSerializer(config, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        return Response(self.payload(data.save()))


class KnowledgeSerializer(serializers.ModelSerializer):
    answer = serializers.CharField(max_length=3000, allow_blank=True)

    class Meta:
        model = KnowledgeEntry
        fields = ['id', 'question', 'answer', 'category', 'active']

    def validate(self, values):
        question = values.get('question', self.instance.question if self.instance else '')
        answer = values.get('answer', self.instance.answer if self.instance else '')
        active = values.get('active', self.instance.active if self.instance else True)
        if active and not answer.strip():
            raise serializers.ValidationError({'answer': 'Enter an approved answer before activating this FAQ.'})
        business = self.context['view'].business
        existing = KnowledgeEntry.objects.filter(business=business)
        if self.instance:
            existing = existing.exclude(id=self.instance.id)
        if any(normalize(question) == normalize(item) for item in existing.values_list('question', flat=True).iterator()):
            raise serializers.ValidationError({'question': 'This workspace already has that question.'})
        return values


def normalize(value):
    return ' '.join(value.casefold().rstrip(' ?.!').split())


STARTER_QUESTIONS = (
    'What services do you offer?', 'How can I contact customer support?', 'What are your business hours?',
    'How can I request a quotation?', 'How long does a project usually take?', 'Do you offer customized solutions?',
    'Can I schedule a consultation?', 'How do I report a problem?', 'Can I speak with a human agent?',
    'How can I track my request?', 'What information do you need to start a project?',
    'Do you provide ongoing support?', 'How do I share feedback?', 'What happens after I submit an enquiry?',
    'Can I get assistance with an existing service?',
)


class KnowledgeViewSet(WorkspaceMixin, viewsets.ModelViewSet):
    serializer_class = KnowledgeSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()

    def get_queryset(self):
        result = KnowledgeEntry.objects.filter(business=self.business).order_by('category', 'id')
        search = self.request.query_params.get('search', '')[:120]
        if self.action == 'list' and search:
            result = result.filter(Q(question__icontains=search) | Q(answer__icontains=search) | Q(category__icontains=search))
        return result

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        Business.objects.select_for_update().get(pk=self.business.pk)
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        Business.objects.select_for_update().get(pk=self.business.pk)
        return super().update(request, *args, **kwargs)

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def starter(self, request, **kwargs):
        Business.objects.select_for_update().get(pk=self.business.pk)
        existing = {normalize(item) for item in self.get_queryset().values_list('question', flat=True)}
        entries = [
            KnowledgeEntry(business=self.business, question=question, answer='', category='Starter draft', active=False)
            for question in STARTER_QUESTIONS if normalize(question) not in existing
        ]
        KnowledgeEntry.objects.bulk_create(entries)
        return Response({'created': len(entries)})

    def perform_create(self, serializer):
        serializer.save(business=self.business)
