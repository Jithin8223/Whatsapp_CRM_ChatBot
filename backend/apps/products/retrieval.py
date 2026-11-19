import re
from difflib import SequenceMatcher
from django.db.models import Q

from .models import Product, ProductDocument, ProductFAQ


def identify_products(business_id, text):
    """Identify only active products in this workspace; ties require clarification."""
    normalized = ' ' + ' '.join(re.findall(r'[\w]+', text.casefold())) + ' '
    words = set(re.findall(r'[\w]+', text.casefold()))
    rows = list(Product.objects.filter(business_id=business_id, active=True).order_by('id'))
    # Aliases are optional structured catalog metadata, stored in specifications.
    # A few common intent phrases let broad questions use the same tenant catalog.
    if any(term in text.casefold() for term in ('cheapest', 'lowest price', 'most affordable', 'all products', 'what services', 'what products', 'what do you offer')):
        if 'cheapest' in text.casefold() or 'lowest price' in text.casefold() or 'most affordable' in text.casefold():
            priced = [item for item in rows if item.price is not None]
            return ([min(priced, key=lambda item: item.price)] if priced else rows[:1]), False
        return rows[:8], False
    use_cases = {'shop': ('e-commerce', 'online store'), 'store': ('e-commerce', 'online store'), 'sell': ('e-commerce', 'online store')}
    if words & set(use_cases):
        hints = set(sum((list(use_cases[word]) for word in words & set(use_cases)), []))
        matches = [item for item in rows if any(hint in (item.name + ' ' + item.description).casefold() for hint in hints)]
        if matches:
            return matches[:5], False
    ranked = []
    named = []
    for product in rows:
        row = {'id': product.id, 'name': product.name}
        aliases = product.specifications.get('aliases', []) if isinstance(product.specifications, dict) else []
        candidates = [product.name, *[alias for alias in aliases if isinstance(alias, str)]]
        if any(' ' + ' '.join(re.findall(r'[\w]+', candidate.casefold())) + ' ' in normalized for candidate in candidates):
            named.append(row['id'])
            continue
        name = ' ' + ' '.join(re.findall(r'[\w]+', row['name'].casefold())) + ' '
        name_words = set(name.split())
        overlap = words & name_words
        fuzzy = any(
            len(word) >= 5 and len(candidate) >= 5 and SequenceMatcher(None, word, candidate).ratio() >= 0.78
            for word in words for candidate in name_words
        )
        if overlap or fuzzy:
            ranked.append((max(len(overlap) / max(1, len(name_words)), 0.5 if fuzzy else 0), row['id']))
    if named:
        return list(Product.objects.filter(business_id=business_id, active=True, id__in=named).order_by('id')), False
    ranked = [row for row in ranked if row[0] >= 0.5]
    if not ranked:
        return [], False
    best = max(score for score, _ in ranked)
    ids = [item_id for score, item_id in ranked if score == best]
    return list(Product.objects.filter(business_id=business_id, active=True, id__in=ids).order_by('id')), len(ids) > 1


def product_knowledge(business_id, text):
    products, ambiguous = identify_products(business_id, text)
    if ambiguous:
        return products, [], True
    if not products and any(term in text.casefold() for term in ('cheapest', 'lowest price', 'most affordable', 'all products', 'what services', 'what products', 'what do you offer', 'services do you offer')):
        # General service questions use approved FAQs and a small catalog sample.
        products = list(Product.objects.filter(business_id=business_id, active=True).order_by('id')[:8])
    entries = []
    budget = 10000
    ids = [item.id for item in products]
    for product in products:
        facts = [f'Price: {f"{product.price} {product.currency}".strip() if product.price is not None else "Not configured"}']
        facts.append(f'Availability: {product.get_availability_display() if product.availability != "unknown" else "Not configured"}')
        facts.append(f'Warranty: {product.warranty.strip() or "Not configured"}')
        if product.features: facts.append('Features: ' + '; '.join(product.features)[:500])
        if product.specifications: facts.append('Specifications: ' + '; '.join(f'{key}: {value}' for key, value in product.specifications.items())[:500])
        if product.description.strip(): facts.append(f'Description: {product.description.strip()[:500]}')
        if product.additional_info.strip(): facts.append('Additional facts: ' + product.additional_info.strip()[:800])
        answer = '\n'.join(facts)[:1400]
        if budget:
            answer = answer[:budget]
            entries.append({'kind': 'product', 'product_id': str(product.id), 'question': f'Product: {product.name}', 'answer': answer})
            budget -= len(answer)
    if budget:
        faq_rows = ProductFAQ.objects.filter(business_id=business_id, active=True)
        if ids:
            faq_rows = faq_rows.filter(product_id__in=ids)
        else:
            # Approved FAQ questions may answer common requests without a named
            # product (quotation, human support), always within this workspace.
            faq_rows = faq_rows.filter(Q(question__icontains='quotation') | Q(question__icontains='quote'))
        for row in faq_rows.order_by('id').values('question', 'answer')[:40]:
            if not budget: break
            answer = row['answer'][:700][:budget]
            entries.append({'question': row['question'][:240], 'answer': answer})
            budget -= len(answer)
    if budget:
        for row in ProductDocument.objects.filter(business_id=business_id, product_id__in=ids).order_by('id').values('title', 'content')[:10]:
            if not budget: break
            answer = row['content'][:1800][:budget]
            entries.append({'question': f'Product documentation: {row["title"][:160]}', 'answer': answer})
            budget -= len(answer)
    return products, entries, False
