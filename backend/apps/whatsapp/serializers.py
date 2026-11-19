from rest_framework import serializers
from .models import Connection


class ConnectionSerializer(serializers.ModelSerializer):
    operational = serializers.BooleanField(source='whatsapp_ready', read_only=True)
    # The provider reports a signed-in device (session may still be unusable, e.g. disconnected by an admin).
    connected = serializers.SerializerMethodField()
    workspace_ai_enabled = serializers.SerializerMethodField()

    def get_connected(self, obj):
        return obj.state == 'connected'
    auto_reply_active = serializers.SerializerMethodField()

    def get_workspace_ai_enabled(self, obj):
        from apps.ai_engine.models import AISettings
        cache = self.context.setdefault('_workspace_ai', {})
        if obj.business_id not in cache:
            cache[obj.business_id] = AISettings.objects.filter(business_id=obj.business_id, enabled=True).exists()
        return cache[obj.business_id]

    def get_auto_reply_active(self, obj):
        """True only when an incoming real WhatsApp message will be answered automatically."""
        return bool(obj.ai_enabled and obj.whatsapp_ready and self.get_workspace_ai_enabled(obj))

    class Meta:
        model = Connection
        fields = ['id', 'name', 'provider', 'state', 'desired_state', 'last_error', 'checked_at', 'created_at', 'ai_enabled',
                  'operational', 'connected', 'workspace_ai_enabled', 'auto_reply_active', 'status_detail', 'state_changed_at']
        read_only_fields = ['id', 'provider', 'state', 'desired_state', 'last_error', 'checked_at', 'created_at', 'ai_enabled',
                            'status_detail', 'state_changed_at']
