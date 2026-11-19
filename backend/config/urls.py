from django.urls import path
from config.health import live, ready, infrastructure, status as health_status
from apps.accounts.views import CSRFView, RegisterView, LoginView, LogoutView, MeView
from apps.tenants.views import WorkspacesView, WorkspaceView, TeamView, TeamMemberView
from rest_framework.routers import SimpleRouter
from django.urls import include
from apps.whatsapp.views import ConnectionViewSet
from apps.conversations.views import ContactViewSet, ConversationViewSet, DashboardView
from apps.conversations.webhooks import WebhookView
from apps.conversations.followups import FollowUpViewSet
from apps.conversations.guest import GuestAttachment, GuestConfig, GuestReceipts, GuestMessages, GuestHandoff, GuestProfile, PublicWhatsAppLink, WidgetSettings
from apps.ai_engine.views import AISettingsView, KnowledgeViewSet
from apps.conversations.simulation import SimulationView
from apps.products.views import ProductViewSet, ProductFAQViewSet, ProductDocumentViewSet, ProductLeadViewSet

router = SimpleRouter()
router.register('connections', ConnectionViewSet, basename='connection')
router.register('contacts', ContactViewSet, basename='contact')
router.register('conversations', ConversationViewSet, basename='conversation')
router.register('knowledge', KnowledgeViewSet, basename='knowledge')
router.register('products', ProductViewSet, basename='product')
router.register('product-faqs', ProductFAQViewSet, basename='product-faq')
router.register('product-documents', ProductDocumentViewSet, basename='product-document')
router.register('leads', ProductLeadViewSet, basename='product-lead')
router.register('followups', FollowUpViewSet, basename='followup')

urlpatterns = [
    path('api/workspaces/<uuid:business_id>/simulation/', SimulationView.as_view()),
    path('api/workspaces/<uuid:business_id>/simulation/<uuid:conversation_id>/', SimulationView.as_view()),
    path('api/guest/<uuid:key>/', GuestConfig.as_view()),
    path('api/guest/<uuid:key>/messages/', GuestMessages.as_view()),
    path('api/guest/<uuid:key>/handoff/', GuestHandoff.as_view()),
    path('api/guest/<uuid:key>/profile/', GuestProfile.as_view()),
    path('api/guest/<uuid:key>/receipts/', GuestReceipts.as_view()),
    path('api/guest/<uuid:key>/attachments/<uuid:attachment_id>/', GuestAttachment.as_view()),
    path('api/guest/<uuid:key>/whatsapp-link/', PublicWhatsAppLink.as_view()),
    path('api/workspaces/<uuid:business_id>/widget/', WidgetSettings.as_view()),
    path('api/workspaces/<uuid:business_id>/ai/', AISettingsView.as_view()),
    path('internal/wppconnect/', WebhookView.as_view()),
    path('api/workspaces/<uuid:business_id>/dashboard/', DashboardView.as_view()),
    path('api/workspaces/<uuid:business_id>/', include(router.urls)),
    path('api/auth/csrf/', CSRFView.as_view()),
    path('api/auth/register/', RegisterView.as_view()),
    path('api/auth/login/', LoginView.as_view()),
    path('api/auth/logout/', LogoutView.as_view()),
    path('api/auth/me/', MeView.as_view()),
    path('api/workspaces/', WorkspacesView.as_view()),
    path('api/workspaces/<uuid:business_id>/', WorkspaceView.as_view()),
    path('api/workspaces/<uuid:business_id>/team/', TeamView.as_view()),
    path('api/workspaces/<uuid:business_id>/team/<int:member_id>/', TeamMemberView.as_view()),
    path('api/health/live/', live),
    path('api/health/ready/', ready),
    path('api/health/infrastructure/', infrastructure),
    path('api/health/status/', health_status),
]
