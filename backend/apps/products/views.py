from pathlib import PurePath

from django.db import transaction
from django.db.models import Q
from rest_framework import serializers, viewsets, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework import status

from apps.conversations.models import Conversation
from apps.tenants.models import Business, Membership
from apps.tenants.permissions import WorkspaceMixin
from .models import Product, ProductFAQ, ProductDocument, ProductLead


class ProductFAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductFAQ
        fields = ['id', 'product', 'question', 'answer', 'active']
        read_only_fields = ['id']

    def validate_product(self, value):
        if value.business_id != self.context['view'].business.id:
            raise serializers.ValidationError('Choose a product from this workspace.')
        return value


class ProductSerializer(serializers.ModelSerializer):
    faqs = ProductFAQSerializer(many=True, read_only=True)
    documents = serializers.SerializerMethodField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True, required=False)

    class Meta:
        model = Product
        fields = ['id', 'name', 'description', 'price', 'currency', 'image_url', 'features', 'specifications',
                  'availability', 'warranty', 'additional_info', 'active', 'faqs', 'documents', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_currency(self, value):
        value = value.upper()
        if value and not value.isalpha():
            raise serializers.ValidationError('Use a three-letter currency code.')
        if len(value) not in (0, 3):
            raise serializers.ValidationError('Use a three-letter currency code.')
        return value

    def validate_image_url(self, value):
        if value and not value.lower().startswith('https://'):
            raise serializers.ValidationError('Product images must use HTTPS URLs.')
        return value

    def validate_features(self, value):
        if not isinstance(value, list) or len(value) > 50 or any(not isinstance(item, str) or len(item) > 300 for item in value):
            raise serializers.ValidationError('Enter up to 50 feature descriptions, each up to 300 characters.')
        return value

    def validate_specifications(self, value):
        if not isinstance(value, dict) or len(value) > 50 or any(not isinstance(k, str) or not isinstance(v, (str, int, float, bool)) for k, v in value.items()):
            raise serializers.ValidationError('Specifications must be a simple object with up to 50 text or numeric values.')
        return value

    def get_documents(self, obj):
        return [{'id': item.id, 'title': item.title} for item in obj.documents.filter(business_id=obj.business_id)]


class ProductViewSet(WorkspaceMixin, viewsets.ModelViewSet):
    serializer_class = ProductSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()

    def get_queryset(self):
        result = Product.objects.filter(business=self.business).prefetch_related('faqs')
        search = self.request.query_params.get('search', '')[:120]
        if search:
            result = result.filter(Q(name__icontains=search) | Q(description__icontains=search))
        return result

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        Business.objects.select_for_update().get(pk=self.business.pk)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(business=self.business)

    @action(detail=True, methods=['get', 'post'], url_path='documents')
    def documents(self, request, pk=None, **kwargs):
        product = self.get_object()
        rows = ProductDocument.objects.filter(product=product, business=self.business)
        if request.method == 'GET':
            return Response([{'id': row.id, 'title': row.title, 'created_at': row.created_at} for row in rows])
        upload = request.FILES.get('file')
        if not upload:
            raise serializers.ValidationError({'file': 'Choose a UTF-8 .txt or .md file.'})
        filename = PurePath(upload.name or '').name
        if PurePath(filename).suffix.lower() not in ('.txt', '.md') or upload.size > 20 * 1024:
            raise serializers.ValidationError({'file': 'Only .txt and .md files up to 20 KB are supported.'})
        try:
            content = upload.read(20 * 1024 + 1).decode('utf-8-sig')
        except (UnicodeDecodeError, AttributeError):
            raise serializers.ValidationError({'file': 'The file must contain UTF-8 text.'}) from None
        if len(content.encode('utf-8')) > 20 * 1024 or '\x00' in content or not content.strip():
            raise serializers.ValidationError({'file': 'The file is empty or exceeds the supported text limit.'})
        row = ProductDocument.objects.create(
            business=self.business, product=product, title=filename[:160], content=content,
        )
        return Response({'id': row.id, 'title': row.title, 'created_at': row.created_at}, status=status.HTTP_201_CREATED)


class ProductFAQViewSet(WorkspaceMixin, viewsets.ModelViewSet):
    serializer_class = ProductFAQSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()

    def get_queryset(self):
        return ProductFAQ.objects.filter(business=self.business).select_related('product').order_by('id')

    def perform_create(self, serializer):
        product = serializer.validated_data['product']
        if product.business_id != self.business.id:
            raise PermissionDenied('Product is outside this workspace.')
        serializer.save(business=self.business)


class ProductDocumentViewSet(WorkspaceMixin, viewsets.GenericViewSet):
    queryset = ProductDocument.objects.all()
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()

    def get_queryset(self):
        return ProductDocument.objects.filter(business=self.business)

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LeadSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(source='conversation.contact.display_name', read_only=True)
    contact_phone = serializers.CharField(source='conversation.contact.crm_phone', read_only=True)
    contact_email = serializers.CharField(source='conversation.contact.crm_email', read_only=True)
    conversation_id = serializers.UUIDField(source='conversation.id', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    source = serializers.SerializerMethodField()
    guest_label = serializers.CharField(source='conversation.contact.guest_label', read_only=True)
    assigned_agent_name = serializers.CharField(source='assigned_agent.username', read_only=True, default=None)
    conversation_status = serializers.CharField(source='conversation.crm_status', read_only=True)
    next_followup = serializers.SerializerMethodField()

    class Meta:
        model = ProductLead
        fields = ['id', 'conversation_id', 'contact_name', 'contact_phone', 'contact_email', 'guest_label', 'source',
                  'product', 'product_name', 'summary', 'status', 'priority', 'assigned_agent', 'assigned_agent_name',
                  'conversation_status', 'next_followup', 'converted_at', 'created_at', 'updated_at']
        read_only_fields = ['converted_at']

    def get_source(self, obj):
        return 'website' if obj.conversation.connection.provider == 'website' else 'whatsapp'

    def get_next_followup(self, obj):
        row = obj.conversation.followups.filter(status='pending').order_by('due_date', 'due_time').values('id', 'due_date').first()
        return row

    def validate_product(self, value):
        if value and value.business_id != self.context['view'].business.id:
            raise serializers.ValidationError('Choose a product from this workspace.')
        return value

    def validate_assigned_agent(self, value):
        if value and not Membership.objects.filter(business=self.context['view'].business, user=value, user__is_active=True).exists():
            raise serializers.ValidationError('Choose an active member of this workspace.')
        return value


class ProductLeadViewSet(WorkspaceMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                         mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = LeadSerializer

    def get_queryset(self):
        params = self.request.query_params
        result = ProductLead.objects.filter(business=self.business, conversation__business=self.business, deleted_at__isnull=True).select_related(
            'conversation__contact', 'conversation__connection', 'product', 'assigned_agent')
        if params.get('status') in dict(ProductLead.STATUSES):
            result = result.filter(status=params['status'])
        if params.get('priority') in dict(ProductLead.PRIORITIES):
            result = result.filter(priority=params['priority'])
        if params.get('source') == 'website':
            result = result.filter(conversation__connection__provider='website')
        elif params.get('source') == 'whatsapp':
            result = result.exclude(conversation__connection__provider='website')
        assignee = params.get('assignee', '')
        if assignee == 'me':
            result = result.filter(assigned_agent=self.request.user)
        elif assignee == 'none':
            result = result.filter(assigned_agent__isnull=True)
        elif assignee.isdigit():
            result = result.filter(assigned_agent_id=int(assignee))
        search = params.get('search', '')[:120]
        if search:
            # Phone numbers are encrypted at rest, so search covers names, WhatsApp IDs, products and notes.
            result = result.filter(Q(conversation__contact__display_name__icontains=search)
                                   | Q(conversation__contact__whatsapp_id__icontains=search)
                                   | Q(product__name__icontains=search) | Q(summary__icontains=search))
        return result.order_by('-updated_at')

    def partial_update(self, request, *args, **kwargs):
        return self._update(request, **kwargs)

    def update(self, request, *args, **kwargs):
        return self._update(request, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """Soft-delete one lead (administrators only). The conversation, its messages, contact and
        follow-ups are kept; follow-ups simply stop counting as lead follow-ups."""
        self.require_admin()
        item = ProductLead.objects.select_for_update().get(pk=self.get_object().pk, business=self.business, deleted_at__isnull=True)
        from django.utils import timezone
        from apps.tenants.models import AuditEvent
        from apps.notifications.events import publish
        item.deleted_at = timezone.now()
        item.deleted_by = request.user
        item.save(update_fields=['deleted_at', 'deleted_by', 'updated_at'])
        AuditEvent.objects.create(business=self.business, actor=request.user, action='lead.deleted', object_id=str(item.id))
        publish(self.business.id, 'lead.updated', item.id)
        publish(self.business.id, 'conversation.updated', item.conversation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    def _update(self, request, **kwargs):
        item = ProductLead.objects.select_for_update().get(pk=self.get_object().pk, business=self.business)
        if self.member.role != 'admin':
            if item.assigned_agent_id not in (None, request.user.id):
                raise PermissionDenied('This lead is assigned to another team member.')
            if request.data.get('assigned_agent') not in (None, '', request.user.id):
                raise PermissionDenied('Agents can only assign leads to themselves.')
        data = LeadSerializer(item, data=request.data, partial=True, context={'view': self})
        data.is_valid(raise_exception=True)
        data.save()
        from apps.tenants.models import AuditEvent
        AuditEvent.objects.create(business=self.business, actor=request.user, action='lead.updated', object_id=str(item.id))
        from apps.notifications.events import publish
        publish(self.business.id, 'lead.updated', item.id)
        return Response(data.data)
