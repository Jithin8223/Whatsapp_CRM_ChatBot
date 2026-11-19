"""Run inside the backend container: python scripts/verify_infrastructure.py."""
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

import httpx
from channels.layers import get_channel_layer
from apps.notifications.tasks import infrastructure_ping


async def check_channels():
    layer = get_channel_layer()
    channel = await layer.new_channel()
    nonce = uuid4().hex
    await layer.send(channel, {'type': 'infrastructure.ping', 'nonce': nonce})
    response = await asyncio.wait_for(layer.receive(channel), timeout=5)
    assert response['nonce'] == nonce, 'Channels round trip failed'
    await layer.close_pools()


for path in ('live', 'ready', 'infrastructure'):
    response = httpx.get(f'http://backend:8000/api/health/{path}/', timeout=15)
    response.raise_for_status()
    assert response.json()['status'] == 'ok'
    print(f'{path}: passed')
result = infrastructure_ping.delay().get(timeout=30)
assert result == {'status': 'ok', 'service': 'celery'}, result
print('Celery broker -> worker -> result backend: passed')
asyncio.run(check_channels())
print('Channels Redis round trip: passed')
