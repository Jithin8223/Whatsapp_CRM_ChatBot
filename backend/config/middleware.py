import ipaddress
import re
from io import BytesIO

from django.conf import settings
from django.http import JsonResponse


UPLOAD_PATH = re.compile(r'^/api/workspaces/[0-9a-f-]{36}/conversations/[0-9a-f-]{36}/attachments/$')


class TrustedClientIPMiddleware:
    """Use the visitor IP from a trusted reverse proxy (e.g. Cloudflare's CF-Connecting-IP)
    for rate limiting and logs. The header is honoured only when the direct peer is inside
    TRUSTED_PROXY_NETWORKS (the private Docker network behind cloudflared/nginx), so
    clients cannot spoof it. Disabled unless TRUSTED_CLIENT_IP_HEADER is set."""

    def __init__(self, get_response):
        self.get_response = get_response
        header = getattr(settings, 'TRUSTED_CLIENT_IP_HEADER', '')
        self.meta_key = 'HTTP_' + header.upper().replace('-', '_') if header else ''
        self.networks = [ipaddress.ip_network(item, strict=False) for item in getattr(settings, 'TRUSTED_PROXY_NETWORKS', [])]

    def __call__(self, request):
        if self.meta_key:
            try:
                peer = ipaddress.ip_address(request.META.get('REMOTE_ADDR', ''))
                client = ipaddress.ip_address(request.META.get(self.meta_key, '').strip())
            except ValueError:
                peer = client = None
            if client and any(peer in network for network in self.networks):
                request.META['REMOTE_ADDR'] = str(client)
        return self.get_response(request)


class BodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limit = 262144
        if request.method == 'POST' and UPLOAD_PATH.match(request.path):
            # The authenticated attachment upload is the only route allowed larger bodies.
            limit = getattr(settings, 'ATTACHMENT_MAX_BYTES', 10 * 1024 * 1024) + 65536
        try:
            length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return JsonResponse({'detail': 'Invalid request size.'}, status=400)
        if length < 0 or length > limit:
            return JsonResponse({'detail': 'Request is too large.'}, status=413)
        if request.method in ('POST', 'PUT', 'PATCH'):
            # Bound bytes even for chunked requests without Content-Length.
            body = request.read(limit + 1)
            if len(body) > limit:
                return JsonResponse({'detail': 'Request is too large.'}, status=413)
            request._body = body
            request._stream = BytesIO(body)
        response = self.get_response(request)
        if request.path.startswith(('/api/', '/internal/', '/ws/')):
            # Private customer data: never cached by Cloudflare, proxies or browsers.
            response['Cache-Control'] = 'no-store, private'
            response['Pragma'] = 'no-cache'
            response.setdefault('X-Content-Type-Options', 'nosniff')
            vary = response.get('Vary', '')
            if 'Cookie' not in vary:
                response['Vary'] = f'{vary}, Cookie' if vary else 'Cookie'
        return response
