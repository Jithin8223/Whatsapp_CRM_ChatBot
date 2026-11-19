import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .guest import guest_session, valid_origin
from .models import WebsiteWidget


class GuestSocket(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.group = None
        self.token = None
        self.deadline = None
        self.origin = dict(self.scope['headers']).get(b'origin', b'').decode('latin1')
        self.key = self.scope['url_route']['kwargs']['key']
        if not valid_origin(self.origin) or not await self.allowed_origin():
            await self.close(code=4403)
            return
        await self.accept()
        self.deadline = asyncio.create_task(self.limit_anonymous_socket())

    @database_sync_to_async
    def allowed_origin(self):
        widget = WebsiteWidget.objects.filter(public_key=self.key, enabled=True).only('origins').first()
        return bool(widget and self.origin in widget.origins)

    async def limit_anonymous_socket(self):
        await asyncio.sleep(5)
        if not self.group:
            await self.close(code=4401)

    @database_sync_to_async
    def authorized(self):
        session = guest_session(self.key, self.token, self.origin)
        if session and self.origin in session.widget.origins:
            return session.conversation_id
        return None

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if not text_data or len(text_data) != 43 or self.token is not None:
            await self.close(code=4400)
            return
        self.token = text_data
        conversation_id = await self.authorized()
        if not conversation_id:
            await self.close(code=4401)
            return
        self.group = f'guest.{conversation_id}'
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.send_json({'kind': 'ready'})

    async def guest_event(self, event):
        if not await self.authorized():
            await self.close(code=4401)
            return
        await self.send_json({'kind': 'updated'})

    async def disconnect(self, code):
        if self.deadline:
            self.deadline.cancel()
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)
