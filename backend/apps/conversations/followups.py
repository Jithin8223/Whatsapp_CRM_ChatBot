"""Follow-up scheduling for conversations and leads (one model shared by the chat
modal, the Follow-ups calendar and the Leads page)."""
from datetime import date

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.notifications.events import publish
from apps.tenants.models import AuditEvent, Membership
from apps.tenants.permissions import WorkspaceMixin
from .models import Conversation, FollowUp

SOURCES = ('whatsapp', 'website', 'lead')


def today_for(request):
    """The viewer's local date (sent by the browser) decides what is overdue; server date otherwise."""
    value = request.query_params.get('today', '')
    try:
        return date.fromisoformat(value) if value else timezone.localdate()
    except ValueError:
        raise serializers.ValidationError({'today': 'Use YYYY-MM-DD.'})


class FollowUpSerializer(serializers.ModelSerializer):
    conversation = serializers.UUIDField(source='conversation_id')
    contact_name = serializers.SerializerMethodField()
    contact_phone = serializers.CharField(source='contact.crm_phone', read_only=True)
    channel = serializers.CharField(source='conversation.connection.channel', read_only=True)
    conversation_status = serializers.CharField(source='conversation.crm_status', read_only=True)
    assigned_agent_name = serializers.CharField(source='assigned_agent.username', read_only=True, default=None)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default=None)
    completed_by_name = serializers.CharField(source='completed_by.username', read_only=True, default=None)
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = FollowUp
        fields = ['id', 'conversation', 'lead', 'contact_name', 'contact_phone', 'channel', 'conversation_status',
                  'customer_said', 'team_said', 'next_action', 'next_steps', 'due_date', 'due_time', 'status', 'priority',
                  'assigned_agent', 'assigned_agent_name', 'created_by', 'created_by_name', 'completed_by_name', 'completed_at',
                  'overdue', 'created_at', 'updated_at']
        read_only_fields = ['lead', 'status', 'completed_at', 'created_by']

    def get_contact_name(self, obj):
        contact = obj.contact
        return contact.display_name or contact.guest_label or contact.crm_phone or 'Customer'

    def get_overdue(self, obj):
        return obj.status == 'pending' and obj.due_date < self.context.get('today', timezone.localdate())

    def validate_assigned_agent(self, value):
        business = self.context['view'].business
        if value and not Membership.objects.filter(business=business, user=value, user__is_active=True).exists():
            raise serializers.ValidationError('Choose an active member of this workspace.')
        return value

    def validate(self, values):
        if not any(values.get(key, getattr(self.instance, key, '')).strip()
                   for key in ('customer_said', 'team_said', 'next_steps')):
            raise serializers.ValidationError('Add at least one note: what the customer said, what you told them, or next steps.')
        return values


class FollowUpViewSet(WorkspaceMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                      mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = FollowUpSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if hasattr(self, 'business'):
            context['today'] = today_for(self.request)
        return context

    def get_queryset(self):
        params = self.request.query_params
        result = FollowUp.objects.filter(business=self.business, conversation__business=self.business).select_related(
            'contact', 'conversation__connection', 'assigned_agent', 'created_by', 'completed_by',
        )
        today = today_for(self.request)
        status = params.get('status')
        if status in ('pending', 'done'):
            result = result.filter(status=status)
        elif status == 'overdue':
            result = result.filter(status='pending', due_date__lt=today)
        assignee = params.get('assignee', '')
        if assignee == 'me':
            result = result.filter(assigned_agent=self.request.user)
        elif assignee == 'none':
            result = result.filter(assigned_agent__isnull=True)
        elif assignee.isdigit():
            result = result.filter(assigned_agent_id=int(assignee))
        source = params.get('source')
        if source == 'lead':
            result = result.filter(lead__isnull=False, lead__deleted_at__isnull=True)
        elif source == 'website':
            result = result.filter(conversation__connection__provider='website')
        elif source == 'whatsapp':
            result = result.exclude(conversation__connection__provider='website')
        for name, lookup in (('date_from', 'due_date__gte'), ('date_to', 'due_date__lte')):
            if params.get(name):
                try:
                    result = result.filter(**{lookup: date.fromisoformat(params[name])})
                except ValueError:
                    raise serializers.ValidationError({name: 'Use YYYY-MM-DD.'})
        if params.get('conversation'):
            try:
                result = result.filter(conversation_id=params['conversation'])
            except Exception:
                raise serializers.ValidationError({'conversation': 'Invalid conversation identifier.'})
        search = params.get('search', '')[:120]
        if search:
            result = result.filter(Q(contact__display_name__icontains=search) | Q(contact__whatsapp_id__icontains=search)
                                   | Q(customer_said__icontains=search) | Q(next_steps__icontains=search))
        order = '-due_date' if params.get('order') == 'recent' else 'due_date'
        return result.order_by('status', order, 'due_time', 'created_at')

    def paginate_queryset(self, queryset):
        # The calendar asks for a whole month at once.
        if self.request.query_params.get('date_from') and self.request.query_params.get('date_to'):
            return None
        return super().paginate_queryset(queryset)

    def _require_editor(self, item):
        if self.member.role != 'admin' and self.request.user.id not in (item.created_by_id, item.assigned_agent_id):
            raise PermissionDenied('Only the assignee, the creator or an administrator can change this follow-up.')

    def _event(self, item, action):
        AuditEvent.objects.create(business=self.business, actor=self.request.user, action=f'followup.{action}', object_id=str(item.id))
        publish(self.business.id, 'followup.updated', item.id)
        publish(self.business.id, 'conversation.updated', item.conversation_id)

    @transaction.atomic
    def perform_create(self, serializer):
        conversation = get_object_or_404(
            Conversation.objects.select_related('contact'), id=serializer.validated_data.pop('conversation_id'),
            business=self.business,
        )
        from apps.products.models import ProductLead
        lead = ProductLead.objects.filter(conversation=conversation, business=self.business, deleted_at__isnull=True).first()
        assigned = serializer.validated_data.pop('assigned_agent', None) or conversation.assigned_agent or self.request.user
        item = serializer.save(business=self.business, conversation=conversation, contact=conversation.contact,
                               lead=lead, assigned_agent=assigned, created_by=self.request.user)
        self._event(item, 'created')

    @transaction.atomic
    def perform_update(self, serializer):
        item = serializer.instance
        self._require_editor(item)
        if 'conversation_id' in serializer.validated_data and serializer.validated_data.pop('conversation_id') != item.conversation_id:
            raise serializers.ValidationError({'conversation': 'A follow-up cannot move to another conversation.'})
        rescheduled = any(key in serializer.validated_data and serializer.validated_data[key] != getattr(item, key)
                          for key in ('due_date', 'due_time'))
        item = serializer.save()
        self._event(item, 'rescheduled' if rescheduled else 'updated')

    @transaction.atomic
    def perform_destroy(self, instance):
        # Admins may delete any follow-up; the creator only their own pending one.
        if self.member.role != 'admin' and not (instance.created_by_id == self.request.user.id and instance.status == 'pending'):
            raise PermissionDenied('Only an administrator, or the creator of a pending follow-up, can delete it.')
        conversation_id = instance.conversation_id
        AuditEvent.objects.create(business=self.business, actor=self.request.user, action='followup.deleted', object_id=str(instance.id))
        instance.delete()
        publish(self.business.id, 'followup.updated', '')
        publish(self.business.id, 'conversation.updated', conversation_id)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def complete(self, request, **kwargs):
        item = FollowUp.objects.select_for_update().get(pk=self.get_object().pk, business=self.business)
        self._require_editor(item)
        if item.status != 'done':
            item.status = 'done'
            item.completed_at = timezone.now()
            item.completed_by = request.user
            item.save(update_fields=['status', 'completed_at', 'completed_by', 'updated_at'])
            self._event(item, 'completed')
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def reopen(self, request, **kwargs):
        item = FollowUp.objects.select_for_update().get(pk=self.get_object().pk, business=self.business)
        self._require_editor(item)
        if item.status != 'pending':
            # The completion is kept in the audit trail; the record itself returns to pending.
            item.status = 'pending'
            item.completed_at = None
            item.completed_by = None
            item.save(update_fields=['status', 'completed_at', 'completed_by', 'updated_at'])
            self._event(item, 'reopened')
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['get'])
    def history(self, request, **kwargs):
        item = self.get_object()
        rows = AuditEvent.objects.filter(business=self.business, object_id=str(item.id), action__startswith='followup.') \
            .select_related('actor').order_by('created_at')
        return Response([{'action': row.action.removeprefix('followup.'), 'actor': row.actor.username if row.actor else None,
                          'at': row.created_at} for row in rows])
