import os
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from channels.sessions import CookieMiddleware
from django.urls import path
from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django_application = get_asgi_application()
from apps.notifications.consumers import WorkspaceConsumer
from apps.conversations.guest_socket import GuestSocket
application = ProtocolTypeRouter({
    'http': django_application,
    'websocket': URLRouter([
        path('ws/workspaces/<uuid:business_id>/', OriginValidator(
            CookieMiddleware(WorkspaceConsumer.as_asgi()), settings.CSRF_TRUSTED_ORIGINS,
        )),
        path('ws/guest/<uuid:key>/', GuestSocket.as_asgi()),
    ]),
})
