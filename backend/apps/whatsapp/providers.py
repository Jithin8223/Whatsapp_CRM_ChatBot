from abc import ABC, abstractmethod
from urllib.parse import quote
import re
import logging
import httpx
from django.conf import settings
from .crypto import decrypt

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Sanitized errors only: never include provider responses or URLs."""


class SafeToRetry(ProviderError):
    """No connection was established, so no send was submitted."""


class AmbiguousSend(ProviderError):
    """Request may have reached WhatsApp; never automatically repeat it."""


class WhatsAppProvider(ABC):
    @abstractmethod
    def generate_token(self):
        """Return backend-only session credentials for encrypted storage."""
        pass

    @abstractmethod
    def start(self): pass

    @abstractmethod
    def status(self): pass

    @abstractmethod
    def close(self, logout=False): pass

    @abstractmethod
    def send_text(self, recipient, text): pass


def message_id(value):
    if isinstance(value, str):
        return value if len(value) <= 255 else ''
    if isinstance(value, dict):
        return message_id(value.get('_serialized') or value.get('id'))
    return ''


class WPPConnectProvider(WhatsAppProvider):
    def __init__(self, connection):
        self.connection = connection
        self.token = decrypt(connection.token_encrypted)

    def request(self, method, endpoint, payload=None, sending=False):
        headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
        try:
            response = httpx.request(method, f'{settings.WPPCONNECT_URL}/api/{self.connection.session}/{endpoint}', json=payload, headers=headers, timeout=httpx.Timeout(20, connect=3), follow_redirects=False)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            logger.warning('WPPConnect request could not connect (sending=%s).', sending)
            raise SafeToRetry('Provider is unreachable.') from None
        except httpx.HTTPError:
            logger.warning('WPPConnect request failed after connection (sending=%s).', sending)
            raise (AmbiguousSend('Delivery outcome is unknown.') if sending else ProviderError('Provider request failed.')) from None
        if not response.is_success:
            logger.warning('WPPConnect rejected request with HTTP %s (sending=%s).', response.status_code, sending)
            raise (AmbiguousSend('Provider did not confirm delivery.') if sending else ProviderError('Provider rejected the request.'))
        try:
            data = response.json()
        except ValueError:
            raise (AmbiguousSend('Provider response was not recognized.') if sending else ProviderError('Provider response was not recognized.')) from None
        if not isinstance(data, dict) or data.get('status') in (False, 'error'):
            raise (AmbiguousSend('Provider did not confirm delivery.') if sending else ProviderError('Provider operation failed.'))
        return data

    def generate_token(self):
        if not settings.WPPCONNECT_SECRET_KEY:
            raise ProviderError('Provider credentials are not configured.')
        data = self.request('POST', f'{quote(settings.WPPCONNECT_SECRET_KEY, safe="")}/generate-token')
        token = data.get('token')
        if not isinstance(token, str) or not token:
            raise ProviderError('Provider token response was invalid.')
        self.token = token
        return token

    def start(self):
        return self.request('POST', 'start-session', {'waitQrCode': False})

    def status(self):
        data = self.request('GET', 'status-session')
        mapping = {'CONNECTED': 'connected', 'ISLOGGED': 'connecting', 'INCHAT': 'connected', 'QRCODE': 'qr', 'INITIALIZING': 'connecting', 'STARTING': 'connecting', 'CLOSED': 'disconnected', 'DISCONNECTED': 'disconnected', 'CLOSING': 'disconnecting', 'ERROR': 'error'}
        state = mapping.get(str(data.get('status', '')).upper(), 'unknown')
        if state == 'connected':
            host = self.request('GET', 'host-device').get('response')
            address = message_id(host.get('phoneNumber')) if isinstance(host, dict) else ''
            if not address.endswith(('@c.us', '@lid', '@s.whatsapp.net')):
                raise ProviderError('Provider did not confirm the signed-in WhatsApp device.')
        return {'state': state, 'qrcode': data.get('qrcode')}

    def close(self, logout=False):
        return self.request('POST', 'logout-session' if logout else 'close-session', {})

    def resolve_recipient(self, recipient):
        """Translate WhatsApp privacy LIDs to a routable phone JID when available."""
        if not recipient.endswith('@lid'):
            return recipient
        try:
            data = self.request('GET', f'contact/pn-lid/{quote(recipient, safe="")}')
        except ProviderError:
            # This read happens before send-message, so retrying cannot duplicate a send.
            raise SafeToRetry('Provider could not resolve the WhatsApp contact.') from None
        phone = message_id(data.get('phoneNumber')) if isinstance(data, dict) else ''
        if not re.fullmatch(r'[1-9]\d{7,14}@c\.us', phone):
            raise SafeToRetry('Provider did not return a routable WhatsApp contact.')
        return phone

    def send_text(self, recipient, text):
        # Server 2.10.81 documents a string but its controller iterates `phone`.
        # A one-item array prevents it from treating the JID as individual characters.
        result = self.request('POST', 'send-message', {'phone': [recipient], 'message': text, 'isGroup': False, 'isLid': recipient.endswith('@lid')}, sending=True)
        return self._sent_identifier(result)

    def send_file(self, recipient, data, mime, filename, caption=''):
        """Send a document/image as a base64 data URI (server JSON limit is 50 MB)."""
        import base64
        uri = f'data:{mime};base64,' + base64.b64encode(data).decode()
        result = self.request('POST', 'send-file', {
            'phone': [recipient], 'base64': uri, 'filename': filename, 'caption': caption or '',
            'isGroup': False, 'isLid': recipient.endswith('@lid'),
        }, sending=True)
        return self._sent_identifier(result)

    @staticmethod
    def _sent_identifier(result):
        values = result.get('response')
        if isinstance(values, list) and len(values) == 1:
            identifier = message_id(values[0].get('id')) if isinstance(values[0], dict) else ''
            if identifier:
                return identifier
        raise AmbiguousSend('Provider did not return a message identifier.')


def provider_for(connection):
    if connection.provider != 'wppconnect':
        raise ProviderError('Unsupported provider.')
    return WPPConnectProvider(connection)
