from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.accounts.views import RegisterSerializer
from .models import Business, Membership, AuditEvent
from .permissions import WorkspaceMixin


def member_data(member):
    return {'id': member.id, 'user_id': member.user_id, 'username': member.user.username, 'role': member.role}


class WorkspacesView(APIView):
    @transaction.atomic
    def post(self, request):
        class Input(serializers.Serializer):
            name = serializers.CharField(max_length=120)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        business = Business.objects.create(**data.validated_data)
        Membership.objects.create(business=business, user=request.user, role='admin')
        return Response({'id': str(business.id), 'name': business.name, 'role': 'admin'}, status=201)


class WorkspaceView(WorkspaceMixin, APIView):
    def get(self, request, **kwargs):
        return Response({'id': str(self.business.id), 'name': self.business.name, 'role': self.member.role})

    def patch(self, request, **kwargs):
        self.require_admin()
        class Input(serializers.Serializer):
            name = serializers.CharField(max_length=120)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        self.business.name = data.validated_data['name']
        self.business.save(update_fields=['name'])
        AuditEvent.objects.create(business=self.business, actor=request.user, action='workspace.updated')
        return self.get(request, **kwargs)


class TeamView(WorkspaceMixin, APIView):
    def get(self, request, **kwargs):
        return Response([member_data(m) for m in self.business.memberships.select_related('user').order_by('id')])

    @transaction.atomic
    def post(self, request, **kwargs):
        self.require_admin()
        class Input(RegisterSerializer):
            business_name = None
            role = serializers.ChoiceField(choices=['admin', 'agent'])
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        from django.db import IntegrityError
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=values['username'], email=values['email'], password=values['password'])
        except IntegrityError:
            raise serializers.ValidationError('Username is unavailable.')
        item = Membership.objects.create(business=self.business, user=user, role=values['role'])
        AuditEvent.objects.create(business=self.business, actor=request.user, action='member.created', object_id=str(item.id))
        return Response(member_data(item), status=201)


class TeamMemberView(WorkspaceMixin, APIView):
    @transaction.atomic
    def patch(self, request, member_id, **kwargs):
        self.require_admin()
        Business.objects.select_for_update().get(id=self.business.id)
        item = get_object_or_404(Membership, id=member_id, business=self.business)
        class Input(serializers.Serializer):
            role = serializers.ChoiceField(choices=['admin', 'agent'])
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        if item.role == 'admin' and data.validated_data['role'] != 'admin' and self.business.memberships.filter(role='admin').count() == 1:
            raise serializers.ValidationError('A workspace must retain an administrator.')
        item.role = data.validated_data['role']
        item.save(update_fields=['role'])
        AuditEvent.objects.create(business=self.business, actor=request.user, action='member.role_changed', object_id=str(item.id))
        from apps.notifications.events import publish
        publish(self.business.id, 'team.updated')
        return Response(member_data(item))

    @transaction.atomic
    def delete(self, request, member_id, **kwargs):
        self.require_admin()
        Business.objects.select_for_update().get(id=self.business.id)
        item = get_object_or_404(Membership, id=member_id, business=self.business)
        if item.role == 'admin' and self.business.memberships.filter(role='admin').count() == 1:
            raise serializers.ValidationError('A workspace must retain an administrator.')
        AuditEvent.objects.create(business=self.business, actor=request.user, action='member.removed', object_id=str(item.id))
        from apps.conversations.models import Conversation
        from apps.notifications.events import publish
        Conversation.objects.filter(business=self.business, assigned_agent_id=item.user_id).update(assigned_agent=None)
        item.delete()
        publish(self.business.id, 'team.updated')
        return Response(status=204)
