"""Team workflow on conversations: viewing, claiming, CRM status, lead conversion,
human takeover and resuming AI.

Four independent facts live on a conversation and are never coupled implicitly:
  assigned_agent  who owns it            (claim / reassign)
  crm_status      sales pipeline status  (Unassigned, Open, Unanswered, Not Interested, Converted)
  reply_mode      who replies            (ai / assisted / manual / waiting)
  status          open or resolved
Viewing changes none of them.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.notifications.events import publish
from apps.tenants.models import AuditEvent, Membership
from .models import Conversation, ConversationView
from .services import cancel_ai_replies

logger = logging.getLogger(__name__)
VIEW_DEDUP_WINDOW = timedelta(minutes=5)


class Conflict(ValidationError):
    status_code = 409
    default_code = 'conflict'


def _locked(business, conversation_id):
    return Conversation.objects.select_for_update(of=('self',)).select_related('connection', 'contact').get(
        id=conversation_id, business=business,
    )


def _audit(business, actor, action, conversation):
    AuditEvent.objects.create(business=business, actor=actor, action=action, object_id=str(conversation.id))
    publish(business.id, 'conversation.updated', conversation.id)


@transaction.atomic
def record_view(business, conversation, user):
    """Record that a member opened the conversation (history is one row per member).

    last_viewed_at always moves, so "Last opened by" names whoever opened it most
    recently; view_count ignores re-opens within VIEW_DEDUP_WINDOW (UI refreshes).
    Never changes the owner, CRM status or reply mode.
    """
    now = timezone.now()
    previous = ConversationView.objects.filter(conversation=conversation).order_by('-last_viewed_at').values_list('user_id', flat=True).first()
    view, created = ConversationView.objects.select_for_update().get_or_create(
        conversation=conversation, user=user, defaults={'business': business, 'last_viewed_at': now},
    )
    if not created:
        fields = ['last_viewed_at']
        if now - view.last_viewed_at >= VIEW_DEDUP_WINDOW:
            view.view_count += 1
            fields.append('view_count')
        view.last_viewed_at = now
        view.save(update_fields=fields)
    if previous != user.id:
        publish(business.id, 'conversation.updated', conversation.id)  # other screens refresh "Last opened by"
    return view, created


@transaction.atomic
def claim(business, member, conversation_id):
    item = _locked(business, conversation_id)
    if item.assigned_agent_id == member.user_id:
        return item
    if item.assigned_agent_id:
        raise Conflict('This conversation has already been claimed by another team member.')
    item.assigned_agent_id = member.user_id
    fields = ['assigned_agent']
    if item.crm_status == 'unassigned':
        item.crm_status = 'open'
        fields.append('crm_status')
    item.save(update_fields=fields)  # reply_mode untouched: claiming keeps automation running
    _audit(business, member.user, 'conversation.claimed', item)
    return item


@transaction.atomic
def assign(business, member, conversation_id, user_id):
    """Administrator (re)assignment. user_id None unassigns."""
    if member.role != 'admin':
        raise PermissionDenied('Only workspace administrators can reassign conversations.')
    item = _locked(business, conversation_id)
    if user_id is not None and not Membership.objects.filter(business=business, user_id=user_id, user__is_active=True).exists():
        raise NotFound('No active member of this workspace has that identifier.')  # never reveal other tenants
    if item.assigned_agent_id == user_id:
        return item
    item.assigned_agent_id = user_id
    fields = ['assigned_agent']
    if user_id and item.crm_status == 'unassigned':
        item.crm_status = 'open'
        fields.append('crm_status')
    if user_id is None and item.reply_mode == 'manual':
        # Nobody owns a human-mode chat any more: surface it as waiting for an agent.
        item.reply_mode = 'waiting'
        fields.append('reply_mode')
    item.save(update_fields=fields)
    _audit(business, member.user, 'conversation.assigned' if user_id else 'conversation.unassigned', item)
    return item


def _can_manage(member, item):
    return member.role == 'admin' or item.assigned_agent_id in (None, member.user_id)


@transaction.atomic
def set_crm_status(business, member, conversation_id, value):
    if value not in dict(Conversation.CRM_STATUSES):
        raise ValidationError({'crm_status': 'Choose a valid status.'})
    item = _locked(business, conversation_id)
    if not _can_manage(member, item):
        raise PermissionDenied('This conversation is assigned to another team member.')
    previous = item.crm_status
    if value == previous and value != 'converted':
        return item
    item.crm_status = value
    item.save(update_fields=['crm_status'])
    if value == 'converted':
        convert_to_lead(business, member.user, item)
    # Leaving Converted keeps the lead and its history; the lead's own status is managed on the Leads page.
    if value != previous:
        _audit(business, member.user, f'conversation.status.{value}', item)
    return item


def convert_to_lead(business, actor, conversation):
    """Create or update the conversation's single lead (OneToOne) inside the caller's transaction."""
    from apps.products.models import Product, ProductLead
    state = conversation.chatbot_state if isinstance(conversation.chatbot_state, dict) else {}
    product = None
    if state.get('product_id'):
        product = Product.objects.filter(business=business, pk=state['product_id']).first()
    channel = 'WhatsApp' if conversation.connection.channel == 'whatsapp' else 'website chat'
    lead = ProductLead.objects.select_for_update().filter(conversation=conversation, business=business).first()
    now = timezone.now()
    if lead is None:
        lead = ProductLead.objects.create(
            business=business, conversation=conversation, product=product, status='converted', converted_at=now,
            assigned_agent_id=conversation.assigned_agent_id or getattr(actor, 'id', None),
            summary=f'Converted from {channel} conversation.',
        )
        created = True
    else:
        # An explicit conversion restores a previously deleted lead (never a passive flow).
        created = lead.deleted_at is not None
        lead.restore()
        lead.status = 'converted'
        lead.converted_at = lead.converted_at or now
        if conversation.assigned_agent_id:
            lead.assigned_agent_id = conversation.assigned_agent_id
        if product and not lead.product_id:
            lead.product = product
        lead.save(update_fields=['status', 'converted_at', 'assigned_agent', 'product', 'deleted_at', 'deleted_by', 'updated_at'])
    AuditEvent.objects.create(business=business, actor=actor, action='lead.converted', object_id=str(lead.id))
    publish(business.id, 'lead.created' if created else 'lead.updated', lead.id)
    logger.info('crm.lead_converted conversation=%s lead=%s created=%s', conversation.id, lead.id, created)
    return lead, created


@transaction.atomic
def takeover(business, member, conversation_id):
    """Explicit human takeover: stop automatic replies and cancel any pending ones."""
    item = _locked(business, conversation_id)
    if not _can_manage(member, item):
        raise PermissionDenied('This conversation is assigned to another team member.')
    cancel_ai_replies(item)
    item.reply_mode = 'manual'
    fields = ['reply_mode']
    if not item.assigned_agent_id:
        item.assigned_agent_id = member.user_id
        fields.append('assigned_agent')
        if item.crm_status == 'unassigned':
            item.crm_status = 'open'
            fields.append('crm_status')
    item.save(update_fields=fields)
    _audit(business, member.user, 'conversation.human_takeover', item)
    return item


@transaction.atomic
def resume_ai(business, member, conversation_id, mode='ai'):
    from apps.ai_engine.models import AISettings
    item = _locked(business, conversation_id)
    if not _can_manage(member, item):
        raise PermissionDenied('This conversation is assigned to another team member.')
    if not item.connection.supports_auto_reply:
        raise ValidationError('Connect an authorized WhatsApp Business session and enable its AI replies first.')
    if not AISettings.objects.filter(business=business, enabled=True).exists():
        raise ValidationError('Enable automatic replies in the chatbot settings before returning to AI.')
    if item.reply_mode != mode:
        cancel_ai_replies(item)
    item.reply_mode = mode
    item.handoff_at = None
    item.save(update_fields=['reply_mode', 'handoff_at'])  # owner kept: resuming AI is not unassigning
    _audit(business, member.user, 'conversation.ai_resumed', item)
    return item
