import asyncio
from uuid import UUID
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken
from apps.accounts.models import AuthSession
from apps.tenants.models import Membership


class WorkspaceConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def authorized(self):
        try:
            token = AccessToken(self.scope.get('cookies', {}).get('crm_access', ''))
            return AuthSession.objects.filter(id=UUID(token['sid']), user_id=token['user_id'], user__is_active=True, expires_at__gt=timezone.now(), user__memberships__business_id=self.business_id).exists()
        except Exception:
            return False

    async def connect(self):
        self.business_id = self.scope['url_route']['kwargs']['business_id']
        self.group = f'business.{self.business_id}'
        self.joined = False
        self.heartbeat_task = None
        if not await self.authorized():
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(self.group, self.channel_name)
        self.joined = True
        await self.accept()
        await self.send_json({'kind': 'ready'})
        self.heartbeat_task = asyncio.create_task(self.heartbeat())

    async def heartbeat(self):
        try:
            while True:
                await asyncio.sleep(20)
                if not await self.authorized():
                    await self.close(code=4401)
                    return
                await self.send_json({'kind': 'heartbeat'})
        except asyncio.CancelledError:
            pass

    async def receive_json(self, content, **kwargs):
        if not await self.authorized():
            await self.close(code=4401)
            return
        # No client-selected groups or message dispatch over WebSocket.
        if isinstance(content, dict) and content.get('kind') == 'ping':
            await self.send_json({'kind': 'pong'})
        else:
            await self.close(code=4400)

    async def crm_event(self, event):
        if not await self.authorized():
            await self.close(code=4401)
            return
        await self.send_json({'kind': event['kind'], 'object_id': event['object_id']})

    async def disconnect(self, code):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.joined:
            await self.channel_layer.group_discard(self.group, self.channel_name)
