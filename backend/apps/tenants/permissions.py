from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from .models import Membership


def membership(user, business_id, admin=False):
    item = get_object_or_404(Membership.objects.select_related('business', 'user'), user=user, business_id=business_id)
    if admin and item.role != 'admin':
        raise PermissionDenied('Workspace administrator access required.')
    return item


class WorkspaceMixin:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.member = membership(request.user, kwargs['business_id'])
        self.business = self.member.business

    def require_admin(self):
        if self.member.role != 'admin':
            raise PermissionDenied('Workspace administrator access required.')
