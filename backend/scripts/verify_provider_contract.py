"""Check installed provider auth/status and private webhook auth, without pairing."""
import os
import sys
from pathlib import Path
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

import httpx
from django.conf import settings
from apps.whatsapp.models import Connection
from apps.whatsapp.providers import WPPConnectProvider

item = Connection(session=f'crm_contract_{uuid.uuid4().hex}', name='Contract check')
adapter = WPPConnectProvider(item)
adapter.generate_token()
status = adapter.status()
assert status['state'] == 'disconnected', 'Unexpected unstarted session state'
print('Installed provider token generation and unstarted session status: passed')
# A valid credential gets to payload validation; no event or session is created.
response = httpx.post('http://backend:8000/internal/wppconnect/', json={}, auth=('wppconnect', settings.WPPCONNECT_WEBHOOK_SECRET), timeout=10)
assert response.status_code == 400, f'Expected authenticated payload validation, got {response.status_code}'
response = httpx.post('http://backend:8000/internal/wppconnect/', json={}, timeout=10)
assert response.status_code == 401
print('Live internal webhook credential verification: passed')
print('No WhatsApp session started or account paired.')
