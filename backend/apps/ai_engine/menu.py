import re

from django.db import transaction

from apps.conversations.models import Contact, Conversation
from apps.notifications.events import publish
from apps.products.models import Product, ProductLead


PAGE_SIZE = 8
GREETING = re.compile(r'(hi+|hii+|hey+|hai|hel+o+|helo|hlo|hola|namaste|start|good (morning|afternoon|evening))( there)?')
MENU_COMMANDS = ('menu', 'main menu', 'restart', 'start over', 'reset')


def normalize(content):
    """Fold case and drop punctuation/emoji so 'Hello!' or 'Menu 👋' match commands."""
    return ' '.join(re.sub(r'[^\w\s]', ' ', (content or '').casefold()).split())


def is_greeting(content):
    return bool(GREETING.fullmatch(normalize(content)))


def is_menu_command(content, greetings=True):
    """A customer request to (re)start the automated menu.

    greetings=False is used to resume a handed-off chat: there "Hello?" usually means
    "is anyone there?", so only an explicit menu/restart/start command brings the bot back.
    """
    folded = normalize(content)
    return folded in MENU_COMMANDS or folded == 'start' or (greetings and bool(GREETING.fullmatch(folded)))


def menu_items(config):
    from .models import default_chatbot_menu
    items = config.chatbot_menu if isinstance(config.chatbot_menu, list) else []
    items = [item for item in items if isinstance(item, dict) and item.get('label')]
    return items or default_chatbot_menu()


def _menu(config, business):
    greeting = (config.chatbot_greeting or '').replace('{business}', business.name) or f'Welcome to {business.name}!'
    lines = [f'{index}. {item["label"]}' for index, item in enumerate(menu_items(config), 1)]
    return (f'{greeting}\n\nHow can we help you?\n\n' + '\n'.join(lines)
            + f'\n\nReply with a number from 1 to {len(lines)}, or type help.')


def _plain_menu(config):
    return '\n'.join(f'{index}. {item["label"]}' for index, item in enumerate(menu_items(config), 1))


@transaction.atomic
def fallback_reply(business_id, conversation_id, config, unsupported=False):
    """Offer the main menu instead of a human handoff when no approved answer exists.

    The state is reset to MAIN_MENU so the customer's next number is interpreted
    against the menu shown in this reply.
    """
    conversation = Conversation.objects.select_for_update().filter(pk=conversation_id, business_id=business_id).first()
    if conversation:
        conversation.chatbot_state = {'state': 'MAIN_MENU'}
        conversation.save(update_fields=['chatbot_state'])
    intro = ('Sorry, I can only read text messages.' if unsupported
             else 'Sorry, I could not find an answer to that.')
    return (f'{intro} Please choose an option:\n\n{_plain_menu(config)}\n\n'
            'Reply with a number, type menu to start again, or type human to talk to our team.')


def _price(product):
    if product.price is None:
        return 'Price: Please contact us for pricing'
    return f'Price: {product.price} {product.currency}'.strip()


def _product_list(products, page=0, prices=True, footer='Reply with a product number to see details.'):
    if not products:
        return 'Our product catalog is currently empty. Please ask us a question or request human assistance.'
    start = page * PAGE_SIZE
    shown = products[start:start + PAGE_SIZE]
    lines = [f'{index + 1}. {product.name}' + (f' — {_price(product)}' if prices else '') for index, product in enumerate(shown)]
    if start + PAGE_SIZE < len(products):
        lines.append('9. More products')
    lines.append('0. Main menu')
    lines.append(footer)
    return '\n'.join(lines)


QUOTE_FOOTER = 'Reply with the number of the product you would like a quotation for.'
LIST_INTENT = re.compile(
    r'\b(what|which|list|show|see|view|all|your|any)\b.*\b(products|catalog|catalogue|plans|packages)\b'
    r'|^(products|catalog|catalogue)$')
PRICE_LIST_INTENT = re.compile(r'^(prices|price list|pricing)$|\b(price list|all prices|your prices)\b')
HELP = ('You can reply with a number from the current list.\n'
        'Type menu or restart for the main menu, back for the previous list, '
        'or ask a question in your own words. Type human to talk to our team.')
EMAIL = re.compile(r'[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'\+?[0-9][0-9 ()-]{6,20}')


def _active_products(business_id):
    return list(Product.objects.filter(business_id=business_id, active=True).order_by('name', 'id'))


def _has_contact_route(conversation):
    """WhatsApp chats can always be answered in-thread; website guests need a phone or email."""
    contact = conversation.contact
    if conversation.connection.channel == 'whatsapp':
        return True
    return bool(contact.mobile_encrypted or contact.email_encrypted)


def _store_contact_details(conversation, text):
    from apps.conversations.mobile import email_cipher, mobile_cipher
    contact = conversation.contact
    email = EMAIL.search(text)
    phone = PHONE.search(text)
    fields = []
    if email:
        contact.email_encrypted = email_cipher().encrypt(email[0].strip().lower().encode()).decode()
        fields.append('email_encrypted')
    if phone:
        digits = re.sub(r'[^0-9]', '', phone[0])
        if re.fullmatch(r'[1-9][0-9]{7,14}', digits):
            contact.mobile_encrypted = mobile_cipher().encrypt(('+' + digits).encode()).decode()
            fields.append('mobile_encrypted')
    if fields:
        contact.save(update_fields=[*fields, 'updated_at'])
    return bool(fields)


def _details(product):
    lines = [product.name, _price(product), f'Availability: {product.get_availability_display()}']
    if product.description:
        lines.append(product.description)
    if product.features:
        lines.append('Features: ' + '; '.join(product.features))
    lines.append('\n1. Request a quotation\n2. View products\n3. Main menu\nType back to return to the list.')
    return '\n'.join(lines)


@transaction.atomic
def handle_chatbot_menu(business_id, conversation_id, config, content):
    """Handle menu commands and numbered choices; return None for natural Q&A."""
    conversation = Conversation.objects.select_for_update().select_related('business', 'contact').filter(
        pk=conversation_id, business_id=business_id, contact__business_id=business_id,
    ).first()
    if not conversation:
        return None
    state = dict(conversation.chatbot_state) if isinstance(conversation.chatbot_state, dict) else {}
    current = state.get('state', 'MAIN_MENU')
    text = content.strip()
    folded = normalize(text)
    if folded.isdigit():
        text = folded  # '1.', '*1*' or ' 1 ' are menu selections too
    options = menu_items(config)
    if is_menu_command(text):
        state = {'state': 'MAIN_MENU'}
        response = _menu(config, conversation.business)
    elif re.search(r'\b(request|need|want|get)\s+(a\s+)?(quote|quotation|estimate)\b', folded):
        state = {'state': 'QUOTATION', 'step': 'product'}
        response = 'Which product are you interested in?\n' + _product_list(_active_products(business_id), footer=QUOTE_FOOTER)
    elif folded == 'back' and current == 'PRODUCT_DETAILS' and state.get('list_state'):
        ids = state.get('product_ids', [])
        products = list(Product.objects.filter(business_id=business_id, active=True, id__in=ids).order_by('name', 'id'))
        page = int(state.get('page', 0))
        list_state = state['list_state']
        state = {'state': list_state, 'page': page, 'product_ids': [str(p.id) for p in products]}
        response = _product_list(products, page=page, prices=list_state != 'PRODUCT_FEATURES')
    elif folded in ('back', '0'):
        state = {'state': 'MAIN_MENU'}
        response = _menu(config, conversation.business)
    elif folded == 'help':
        response = HELP
    elif current == 'QUOTATION':
        step = state.get('step')
        if step == 'product':
            products = _active_products(business_id)
            if text.isdigit() and 1 <= int(text) <= len(products):
                chosen = products[int(text) - 1]
                state.update({'product_id': str(chosen.id), 'step': 'requirements'})
                response = f'What are your requirements for {chosen.name}?'
            else:
                response = 'Please reply with the number of the product you are interested in.\n' + _product_list(products, footer=QUOTE_FOOTER)
        elif step == 'requirements':
            state['requirements'] = text[:350]
            state, response = _next_quotation_step(conversation, state, business_id)
        elif step == 'name':
            name = text[:120]
            Contact.objects.filter(pk=conversation.contact_id, business_id=business_id).update(display_name=name)
            conversation.contact.display_name = name
            state, response = _next_quotation_step(conversation, state, business_id)
        elif step == 'contact':
            if folded == 'skip' or _store_contact_details(conversation, text):
                state['contact_step_done'] = True
                state, response = _next_quotation_step(conversation, state, business_id)
            else:
                response = ('Please share a phone number with country code (for example +91 98765 43210) '
                            'or an email address, or type skip.')
        else:
            state = {'state': 'QUOTATION', 'step': 'product'}
            response = 'Which product are you interested in?\n' + _product_list(_active_products(business_id), footer=QUOTE_FOOTER)
    elif current != 'QUOTATION' and PRICE_LIST_INTENT.search(folded):
        products = _active_products(business_id)
        state = {'state': 'PRICE_ENQUIRY', 'page': 0, 'product_ids': [str(p.id) for p in products]}
        response = _product_list(products, prices=True)
    elif current != 'QUOTATION' and LIST_INTENT.search(folded):
        products = _active_products(business_id)
        state = {'state': 'PRODUCT_LIST', 'page': 0, 'product_ids': [str(p.id) for p in products]}
        response = _product_list(products, prices=True)
    elif re.fullmatch(r'[1-9]', text) and current in ('MAIN_MENU', 'PRODUCT_LIST', 'PRICE_ENQUIRY', 'PRODUCT_FEATURES'):
        choice = int(text)
        if current == 'MAIN_MENU':
            if choice > len(options):
                response = 'That option is not in the menu.\n\n' + _menu(config, conversation.business)
            else:
                action = options[choice - 1].get('action', '')
                if action in ('products', 'prices', 'features'):
                    products = list(Product.objects.filter(business_id=business_id, active=True).order_by('name', 'id'))
                    if action == 'products':
                        state = {'state': 'PRODUCT_LIST', 'page': 0, 'product_ids': [str(p.id) for p in products]}
                        response = _product_list(products, prices=True)
                    elif action == 'prices':
                        state = {'state': 'PRICE_ENQUIRY', 'page': 0, 'product_ids': [str(p.id) for p in products]}
                        response = _product_list(products, prices=True)
                    else:
                        state = {'state': 'PRODUCT_FEATURES', 'page': 0, 'product_ids': [str(p.id) for p in products]}
                        response = _product_list(products, prices=False)
                elif action == 'quotation':
                    state = {'state': 'QUOTATION', 'step': 'product'}
                    response = 'Which product are you interested in?\n' + _product_list(_active_products(business_id), footer=QUOTE_FOOTER)
                elif action == 'business_info':
                    state = {'state': 'BUSINESS_INFO'}
                    response = 'What business information would you like to know? You can ask about contact details, hours, or services.'
                else:
                    state = {'state': 'GENERAL_QUESTION'}
                    response = 'What would you like to know? Ask your question in your own words.'
        else:
            ids = state.get('product_ids', [])
            page = int(state.get('page', 0))
            products = list(Product.objects.filter(business_id=business_id, active=True, id__in=ids).order_by('name', 'id'))
            if choice == 9 and (page + 1) * PAGE_SIZE < len(products):
                state['page'] = page + 1
                response = _product_list(products, page=page + 1, prices=current != 'PRODUCT_FEATURES')
            else:
                index = page * PAGE_SIZE + choice - 1
                if 1 <= choice <= PAGE_SIZE and index < len(products):
                    product = products[index]
                    state = {'state': 'PRODUCT_DETAILS', 'product_id': str(product.id),
                             'list_state': current, 'page': page, 'product_ids': [str(p.id) for p in products]}
                    response = _details(product)
                else:
                    response = 'That option is not in this list. Reply with a listed number or type menu.'
    elif current == 'PRODUCT_DETAILS' and text == '1':
        state.update({'state': 'QUOTATION', 'step': 'requirements'})
        product = Product.objects.filter(business_id=business_id, id=state.get('product_id'), active=True).first()
        response = f'What are your requirements for {product.name}?' if product else 'Which product are you interested in? Type menu to restart.'
    elif current == 'PRODUCT_DETAILS' and text == '2':
        products = list(Product.objects.filter(business_id=business_id, active=True).order_by('name', 'id'))
        state = {'state': 'PRODUCT_LIST', 'page': 0, 'product_ids': [str(p.id) for p in products]}
        response = _product_list(products)
    elif current == 'PRODUCT_DETAILS' and text == '3':
        state = {'state': 'MAIN_MENU'}
        response = _menu(config, conversation.business)
    elif current == 'PRODUCT_DETAILS' and text.isdigit():
        response = ('That option is not available. Reply 1 to request a quotation, 2 to view products, '
                    '3 for the main menu, or type back.')
    elif current in ('BUSINESS_INFO', 'GENERAL_QUESTION', 'PRODUCT_DETAILS') and not text.isdigit():
        return None
    elif current in ('MAIN_MENU', 'PRODUCT_LIST', 'PRICE_ENQUIRY', 'PRODUCT_FEATURES') and text.isdigit():
        response = 'That option is not available. Reply with a listed number, type menu, or ask a question.'
    else:
        return None

    if state.get('state') in ('PRODUCT_LIST', 'PRICE_ENQUIRY', 'PRODUCT_FEATURES') and not state.get('product_ids') \
            or (state.get('state') == 'QUOTATION' and state.get('step') == 'product' and not _active_products(business_id)):
        # Empty catalog: stay on the main menu so the next number means a main-menu option
        # (live issue: every later number answered "That option is not in this list").
        state = {'state': 'MAIN_MENU'}
        response = ('Our product catalog is currently empty. Please choose another option:\n\n'
                    f'{_plain_menu(config)}\n\nReply with a number, or type human to talk to our team.')
    conversation.chatbot_state = state
    conversation.save(update_fields=['chatbot_state'])
    return response, False


def _next_quotation_step(conversation, state, business_id):
    """Ask for whatever the enquiry still needs, then save it. Returns (state, response)."""
    if not conversation.contact.display_name:
        state['step'] = 'name'
        return state, 'What name should we use for this enquiry?'
    if not state.get('contact_step_done') and not _has_contact_route(conversation):
        state['step'] = 'contact'
        return state, ('How should our team contact you? Share a phone number with country code or an email '
                       'address, or type skip.')
    return {'state': 'MAIN_MENU'}, _save_lead(conversation, state, business_id)


def _save_lead(conversation, state, business_id):
    product = Product.objects.filter(business_id=business_id, pk=state.get('product_id')).first()
    if not product:
        return 'I could not confirm that product. Please choose it again or ask a team member for help.'
    summary = (f'Quotation request for {product.name}. Requirements: {state.get("requirements", "Not provided")}.')[:500]
    lead, created = ProductLead.objects.get_or_create(
        business_id=business_id, conversation=conversation,
        defaults={'product': product, 'summary': summary},
    )
    if not created:
        # A completed quotation request is a new, explicit enquiry: it restores a deleted lead.
        lead.restore()
        lead.product = product
        lead.summary = summary
        lead.status = 'new'
        lead.save(update_fields=['product', 'summary', 'status', 'deleted_at', 'deleted_by', 'updated_at'])
    # Workspace notification: realtime event (admin banner + inbox refresh) and an audit trail.
    from apps.tenants.models import AuditEvent
    AuditEvent.objects.create(business_id=business_id, actor=None, action='lead.quotation_requested', object_id=str(lead.id))
    publish(business_id, 'lead.created', lead.id)
    publish(business_id, 'conversation.updated', conversation.id)
    return ('Thanks! We have recorded your quotation enquiry for '
            f'{product.name}. A team member will review your requirements and follow up. This is not a final quotation.\n\n'
            'Type menu to return to the main menu.')
