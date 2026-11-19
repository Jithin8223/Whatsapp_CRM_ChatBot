import logging
import re
from datetime import timedelta
from uuid import UUID

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.conversations.models import Conversation, Message
from apps.conversations.services import enqueue, queue_message
from apps.notifications.events import publish
from .models import AISettings, KnowledgeEntry, ReplyJob
from .providers import PROVIDERS
from .workflows import HANDOFF, classify, route, wants_human
from .menu import fallback_reply, handle_chatbot_menu
from apps.products.retrieval import product_knowledge

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running')
UNSUPPORTED_BODY = '[Unsupported message type]'


def skip_reason(conversation, config):
    """Why this conversation must not get an automatic reply, or '' when it may."""
    if not config:
        return 'workspace_auto_replies_disabled'
    # Ownership (assigned_agent) never blocks automation: claiming a conversation keeps the
    # bot replying. Only an explicit human takeover (reply_mode 'manual') or a handoff stops it.
    if conversation.reply_mode not in ('ai', 'assisted'):
        return f'reply_mode_{conversation.reply_mode}'
    if conversation.status != 'open':
        return 'conversation_closed'
    if not conversation.connection.supports_auto_reply:
        return 'connection_ai_disabled_or_not_connected'
    return ''


def schedule_reply(message, conversation):
    config = AISettings.objects.filter(business_id=conversation.business_id, enabled=True).first()
    reason = skip_reason(conversation, config)
    if reason:
        logger.info('ai.reply not_scheduled conversation=%s reason=%s', conversation.id, reason)
        return
    ReplyJob.objects.create(message=message)
    logger.info('ai.reply scheduled conversation=%s message=%s', conversation.id, message.id)
    transaction.on_commit(lambda: enqueue(run_replies, conversation.business_id, conversation.id))


def mark_handoff(conversation):
    """Explicit escalation: wait for a human. handoff_at distinguishes this from 'waiting'
    rows created while automation was off, which may resume automatically."""
    conversation.reply_mode = 'waiting'
    conversation.handoff_at = timezone.now()
    conversation.save(update_fields=['reply_mode', 'handoff_at'])


def describe_error(error):
    """Exception type and innermost code location only: messages can carry customer data."""
    import traceback
    frames = traceback.extract_tb(error.__traceback__)
    where = f' at {frames[-1].filename.rsplit("/", 1)[-1]}:{frames[-1].lineno}' if frames else ''
    return f'{type(error).__name__}{where}'[:160]


def compose_reply(business_id, conversation, config, message):
    """Shared by real WhatsApp, simulation and website chat. Returns (answer, handoff, source).

    source: menu | knowledge | generative | intake | fallback | handoff. Only
    'generative' text comes from an external model; every other reply is
    deterministic text from the menu, catalog or approved FAQs.
    """
    content = message.content
    if content == UNSUPPORTED_BODY:
        return fallback_reply(business_id, conversation.id, config, unsupported=True), False, 'fallback'
    if wants_human(config, content, conversation.connection.channel == 'whatsapp'):
        return HANDOFF, True, 'handoff'
    direct = handle_chatbot_menu(business_id, conversation.id, config, content)
    if direct:
        return (*direct, 'menu')
    context = context_for(message)
    context[-1] = {'role': 'user', 'content': content[:1000]}
    intent = classify(content)
    if intent in ('human', 'ticket', 'appointment'):
        return route(intent, config, context, [], PROVIDERS[config.provider])
    products, knowledge, ambiguous_product = product_knowledge(business_id, content)
    # A strongly matching approved FAQ must win over a partial or ambiguous product-name
    # match (e.g. "website maintenance" vs products called "... Website").
    faq = knowledge_for(business_id, content)
    if ambiguous_product and not faq:
        names = ', '.join(item.name for item in products[:5])
        return f'Which product did you mean: {names}?', False, 'knowledge'
    knowledge = faq + knowledge if products and not ambiguous_product else faq
    answer, handoff, source = route(intent, config, context, knowledge, PROVIDERS[config.provider],
                                    fallback_provider=PROVIDERS['knowledge'])
    if answer is None:
        return fallback_reply(business_id, conversation.id, config), False, 'fallback'
    return answer, handoff, source


STOP_WORDS = set('the what when where does can you your and with for are how about please do offer need tell me i to my an a'.split())
VARIANTS = {
    'hour': 'hours', 'working': 'business', 'operating': 'business',
    'pricing': 'price', 'prices': 'price', 'cost': 'price',
    'quote': 'quote', 'quotation': 'quote', 'estimate': 'quote',
    'services': 'service', 'products': 'product', 'returns': 'return',
    'refunds': 'refund', 'contacting': 'contact', 'reach': 'contact',
}


def keywords(text):
    return {VARIANTS.get(word, word) for word in re.findall(r'[^\W_]{2,}', text.casefold()) if word not in STOP_WORDS}


def knowledge_for(business_id, content):
    words = keywords(content)
    if not words:
        return []
    matches = Q()
    terms = words | {variant for variant, canonical in VARIANTS.items() if canonical in words}
    for word in sorted(terms)[:24]:
        matches |= Q(question__icontains=word)
    candidates = KnowledgeEntry.objects.filter(matches, business_id=business_id, active=True).exclude(answer='').order_by('id').values('question', 'answer')[:200]
    ranked = []
    for entry in candidates:
        question = keywords(entry['question'])
        overlap = len(words & question)
        if overlap and (words == question or overlap >= 2 or (len(words) == 1 and len(question) <= 3)):
            ranked.append((2 * overlap / (len(words) + len(question)), entry))
    ranked.sort(key=lambda item: -item[0])
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0] and ranked[0][1]['answer'] != ranked[1][1]['answer']:
        return []
    return [entry for _, entry in ranked[:4]]


def context_for(message):
    rows = Message.objects.filter(
        Q(created_at__lt=message.created_at) | Q(created_at=message.created_at, id__lte=message.id),
        conversation_id=message.conversation_id, business_id=message.business_id,
    ).order_by('-created_at', '-id')[:8]
    return [
        {'role': 'user' if row.direction == 'inbound' else 'assistant', 'content': row.content[:1000]}
        for row in reversed(list(rows))
    ]


@shared_task
def run_replies(business_id, conversation_id):
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().filter(pk=conversation_id, business_id=business_id).first()
        if not conversation:
            return
        jobs = ReplyJob.objects.filter(message__conversation=conversation, message__business_id=business_id, state__in=ACTIVE)
        job = jobs.select_related('message').order_by('message__created_at', 'message_id').first()
        if not job:
            return
        config = AISettings.objects.filter(business_id=business_id, enabled=True).first()
        reason = skip_reason(conversation, config)
        if reason:
            skipped = jobs.update(state='skipped', finished_at=timezone.now(), error=reason[:160])
            logger.info('ai.reply skipped conversation=%s jobs=%s reason=%s', conversation_id, skipped, reason)
            publish(business_id, 'conversation.updated', conversation_id)
            return
        stale = job.state == 'running'
        if stale and job.started_at > timezone.now() - timedelta(minutes=2):
            return
        if stale:
            # A worker died mid-reply. Recompute rather than sending a blind handoff.
            logger.warning('ai.reply stale job restarted conversation=%s message=%s', conversation_id, job.pk)
        job.state = 'running'
        job.started_at = timezone.now()
        job.save(update_fields=['state', 'started_at'])

    error = ''
    answer, handoff, source = None, False, ''
    try:
        answer, handoff, source = compose_reply(business_id, conversation, config, job.message)
    except Exception as exc:
        error = describe_error(exc)
        logger.error('ai.reply failed conversation=%s message=%s error=%s', conversation_id, job.pk, error)
    if not error and not (answer or '').strip():
        error = 'EmptyReply'
        logger.error('ai.reply failed conversation=%s message=%s error=%s', conversation_id, job.pk, error)

    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().filter(pk=conversation_id, business_id=business_id).first()
        if not conversation:
            return
        current = ReplyJob.objects.filter(pk=job.pk, state='running').first()
        if not current:
            return
        config = AISettings.objects.filter(business_id=business_id, enabled=True).first()
        reason = skip_reason(conversation, config)
        current.state = 'skipped'
        current.error = reason
        current.source = source
        if error:
            current.state = 'failed'
            current.error = error
        elif not reason:
            if conversation.reply_mode == 'assisted':
                if not handoff:
                    current.draft_content = answer
                    current.state = 'draft'
                else:
                    current.state = 'handoff'
                    mark_handoff(conversation)
            else:
                try:
                    with transaction.atomic():
                        queue_message(business_id, conversation_id, UUID(str(job.pk)), answer, automated=True)
                except ValidationError as exc:
                    # e.g. the WhatsApp session dropped between composing and queueing.
                    current.state = 'failed'
                    current.error = ('Outbound queue rejected: ' + ' '.join(map(str, exc.detail if isinstance(exc.detail, list) else [exc.detail])))[:160]
                    logger.error('ai.reply queue_rejected conversation=%s message=%s error=%s', conversation_id, job.pk, current.error)
                else:
                    if handoff:
                        mark_handoff(conversation)
                    current.state = 'handoff' if handoff else 'answered'
        current.finished_at = timezone.now()
        current.latency_ms = max(0, int((current.finished_at - job.message.created_at).total_seconds() * 1000))
        current.save(update_fields=['state', 'draft_content', 'error', 'source', 'finished_at', 'latency_ms'])
        logger.info('ai.reply finished conversation=%s message=%s state=%s source=%s latency_ms=%s',
                    conversation_id, job.pk, current.state, source, current.latency_ms)
        publish(business_id, 'conversation.updated', conversation_id)
        transaction.on_commit(lambda: enqueue(run_replies, business_id, conversation_id))
