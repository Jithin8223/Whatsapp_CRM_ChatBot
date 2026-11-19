import uuid

from django.conf import settings
from django.db import models

from apps.tenants.models import Business


class Product(models.Model):
    AVAILABILITY = [('unknown', 'Check with the business'), ('in_stock', 'In stock'), ('out_of_stock', 'Out of stock'), ('preorder', 'Preorder')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=160)
    description = models.TextField(max_length=6000, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    features = models.JSONField(default=list, blank=True)
    specifications = models.JSONField(default=dict, blank=True)
    availability = models.CharField(max_length=16, choices=AVAILABILITY, default='unknown')
    warranty = models.CharField(max_length=1000, blank=True)
    additional_info = models.TextField(max_length=12000, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['business', 'name'], name='unique_business_product_name')]
        indexes = [models.Index(fields=['business', 'active', 'name'])]
        ordering = ['name', 'id']


class ProductFAQ(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='product_faqs')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='faqs')
    question = models.CharField(max_length=240)
    answer = models.TextField(max_length=3000)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['product', 'question'], name='unique_product_faq_question')]
        indexes = [models.Index(fields=['business', 'product', 'active'])]
        ordering = ['id']


class ProductDocument(models.Model):
    """Text extracted from an uploaded UTF-8 .txt/.md file; raw uploads are not retained."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='product_documents')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='documents')
    title = models.CharField(max_length=160)
    content = models.TextField(max_length=20000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['business', 'product'])]
        ordering = ['id']


class ProductLead(models.Model):
    STATUSES = [('new', 'New'), ('follow_up', 'Needs follow-up'), ('qualified', 'Qualified'), ('converted', 'Converted'), ('closed', 'Closed')]
    PRIORITIES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='product_leads')
    # One lead per conversation (OneToOne): conversion and chatbot enquiries update it in place.
    conversation = models.OneToOneField('conversations.Conversation', on_delete=models.CASCADE, related_name='product_lead')
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name='leads')
    summary = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default='new')
    priority = models.CharField(max_length=8, choices=PRIORITIES, default='medium')
    converted_at = models.DateTimeField(null=True, blank=True)
    # Soft deletion: the row stays as a tombstone so passive flows (e.g. a website visitor naming
    # a product) cannot silently recreate a lead an administrator removed. Explicit actions
    # (status Converted, a new quotation request) restore it. Conversations are never touched.
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
    assigned_agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['business', 'status', 'created_at'])]
