from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_engine.models import AISettings
from apps.tenants.models import Business
from apps.tenants.permissions import WorkspaceMixin
from apps.whatsapp.models import Connection
from .models import Contact, Conversation
from .services import record_inbound


class SimulationView(WorkspaceMixin, APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()
        if not settings.DEBUG:
            raise Http404

    @transaction.atomic
    def post(self, request, business_id, conversation_id=None):
        if conversation_id:
            item = get_object_or_404(
                Conversation, pk=conversation_id, business=self.business,
                connection__business=self.business, contact__business=self.business,
                connection__provider=Connection.SIMULATION_PROVIDER,
            )

            class MessageInput(serializers.Serializer):
                id = serializers.UUIDField()
                content = serializers.CharField(max_length=2000)

            data = MessageInput(data=request.data)
            data.is_valid(raise_exception=True)
            _, created = record_inbound(
                business_id, item.connection_id, item.contact_id,
                f'simulation:{item.id}:{data.validated_data["id"]}', data.validated_data['content'],
            )
            return Response(status=201 if created else 200)

        class StartInput(serializers.Serializer):
            number = serializers.CharField(max_length=40, trim_whitespace=True)

            def validate_number(self, value):
                import re
                if not re.fullmatch(r'\+?[0-9 ()-]+', value):
                    raise serializers.ValidationError('Enter an international phone number using digits, spaces, +, parentheses, or hyphens.')
                digits = re.sub(r'[^0-9]', '', value)
                if not re.fullmatch(r'[1-9][0-9]{7,14}', digits):
                    raise serializers.ValidationError('Enter 8 to 15 digits, starting with a country code.')
                return digits

        data = StartInput(data=request.data)
        data.is_valid(raise_exception=True)
        number = data.validated_data['number']
        Business.objects.select_for_update().get(pk=business_id)
        endpoint, _ = Connection.objects.get_or_create(
            business=self.business, provider=Connection.SIMULATION_PROVIDER,
            defaults={'name': 'WhatsApp simulation', 'state': 'connected', 'desired_state': 'open'},
        )
        contact, _ = Contact.objects.get_or_create(
            business=self.business, whatsapp_id=number + '@simulation',
            defaults={'display_name': 'Simulation customer', 'phone': '+' + number},
        )
        enabled = AISettings.objects.filter(business=self.business, enabled=True).exists()
        item, _ = Conversation.objects.get_or_create(
            business=self.business, connection=endpoint, contact=contact,
            defaults={'reply_mode': 'ai' if enabled else 'waiting'},
        )
        return Response({'id': item.id, 'ai_enabled': enabled}, status=201)
