import logging
import re

logger = logging.getLogger(__name__)

HANDOFF = 'I’m passing this conversation to human support.'
INTENTS = {
    'human': re.compile(r'\b(human|real person|live agent|speak to (an? )?agent)\b', re.I),
    'ticket': re.compile(r'\b(create|open|file) (a )?(support )?ticket\b', re.I),
    'appointment': re.compile(r'\b(appointment|book (a )?(call|meeting))\b', re.I),
    'issue': re.compile(r'\b(help with my order|order (hasn.t arrived|is missing|never arrived)|needs? support)\b', re.I),
    'lead': re.compile(r'\b(quote|quotation|estimate|pricing for my business|sales team)\b', re.I),
}
INTAKE = {
    'issue': 'What happened? If this is about an order, you can share its reference. Please leave out payment and account credentials.',
    'lead': 'What product or service are you interested in, and what are you hoping to accomplish?',
}


def classify(content):
    return next((intent for intent, pattern in INTENTS.items() if pattern.search(content)), 'knowledge')


def whatsapp_menu(config, content, context, has_outbound):
    if any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', content, re.I) for term in config.whatsapp_handoff_terms):
        return content, (HANDOFF, True)
    options = config.whatsapp_menu
    if not options:
        return content, None
    menu = '\n'.join(f'{index}. {option["label"]}' for index, option in enumerate(options, 1))
    welcome = f'{config.whatsapp_greeting}\n\n{menu}\n\nReply with a number or type your question.'
    if not has_outbound and re.fullmatch(r'(hi|hello|hey)[!. ]*', content, re.I):
        return content, (welcome, False)
    if re.fullmatch(r'[1-9]', content.strip()):
        index = int(content.strip()) - 1
        latest_answer = next((item['content'] for item in reversed(context) if item['role'] == 'assistant'), '')
        if index < len(options) and latest_answer == welcome:
            question = options[index]['question']
            return (question, None) if question else (content, (HANDOFF, True))
    return content, None


def wants_human(config, content, whatsapp):
    """Explicit escalation: configured WhatsApp phrases or a direct request for a person."""
    terms = config.whatsapp_handoff_terms if whatsapp and isinstance(config.whatsapp_handoff_terms, list) else []
    if any(isinstance(term, str) and term.strip() and re.search(r'(?<!\w)' + re.escape(term.strip()) + r'(?!\w)', content, re.I)
           for term in terms):
        return True
    return bool(INTENTS['human'].search(content))


def route(intent, config, context, knowledge, provider, fallback_provider=None):
    """Return (answer, handoff, source). answer is None when no approved answer exists.

    Only explicit escalation intents hand off. Callers offer the menu instead of a
    handoff when the answer is None, so ordinary questions never strand a customer.
    source is 'knowledge' (approved text verbatim) or 'generative' (external model).
    """
    if intent in ('human', 'ticket', 'appointment'):
        return HANDOFF, True, 'handoff'
    if (intent == 'issue' and config.issue_intake) or (intent == 'lead' and config.lead_intake):
        prompt = INTAKE[intent]
        if not any(item['role'] == 'assistant' and item['content'] == prompt for item in context):
            return prompt, False, 'intake'
    if knowledge:
        used = provider
        try:
            answer = provider.answer(config.instructions, context, knowledge)
        except Exception as error:
            if fallback_provider is None or fallback_provider is provider:
                raise
            # The external model is unavailable: degrade to approved answers verbatim.
            logger.warning('ai.provider_failed error=%s; using approved knowledge answer.', type(error).__name__)
            used = fallback_provider
            answer = fallback_provider.answer(config.instructions, context, knowledge)
        answer = (answer or '').strip()
        if answer and len(answer) <= 4096 and answer != 'HANDOFF':
            return answer, False, getattr(used, 'source', 'knowledge')
    return None, False, ''
