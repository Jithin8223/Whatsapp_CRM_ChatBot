from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.tenants.models import Business, Membership
from .models import User


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class AccountTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)

    def csrf(self):
        return self.client.get('/api/auth/csrf/').json()['csrfToken']

    def register(self):
        return self.client.post('/api/auth/register/', {'username': 'alice', 'email': 'alice@example.com', 'password': 'Correct-Horse-Mango-42!', 'business_name': 'Alice shop'}, format='json', HTTP_X_CSRFTOKEN=self.csrf())

    def test_registration_login_logout_revokes_cookie(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertTrue(self.client.cookies['crm_access']['httponly'])
        self.assertEqual(self.client.get('/api/auth/me/').json()['workspaces'][0]['role'], 'admin')
        cookie = self.client.cookies['crm_access'].value
        self.assertEqual(self.client.post('/api/auth/logout/', HTTP_X_CSRFTOKEN=self.csrf()).status_code, 204)
        self.client.cookies['crm_access'] = cookie
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        result = self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'Correct-Horse-Mango-42!'}, format='json', HTTP_X_CSRFTOKEN=self.csrf())
        self.assertEqual(result.status_code, 200)

    def test_csrf_is_required_for_registration(self):
        self.assertEqual(self.client.post('/api/auth/register/', {}, format='json').status_code, 403)

    def test_anonymous_denied(self):
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)

    def test_weak_password_rejected(self):
        response = self.client.post('/api/auth/register/', {'username': 'alice', 'email': 'a@example.com', 'password': '123', 'business_name': 'Shop'}, format='json', HTTP_X_CSRFTOKEN=self.csrf())
        self.assertEqual(response.status_code, 400)

    def test_workspace_isolation_and_roles(self):
        self.register()
        mine = Membership.objects.get(user__username='alice')
        other = Business.objects.create(name='Other')
        self.assertEqual(self.client.get(f'/api/workspaces/{other.id}/').status_code, 404)
        mine.role = 'agent'
        mine.save()
        url = f'/api/workspaces/{mine.business_id}/'
        self.assertEqual(self.client.patch(url, {'name': 'Changed'}, format='json', HTTP_X_CSRFTOKEN=self.csrf()).status_code, 403)

    def test_admin_team_and_last_admin_guard(self):
        self.register()
        mine = Membership.objects.get(user__username='alice')
        url = f'/api/workspaces/{mine.business_id}/team/'
        response = self.client.post(url, {'username': 'agent', 'email': 'agent@example.com', 'password': 'Correct-Mango-Support-42!', 'role': 'agent'}, format='json', HTTP_X_CSRFTOKEN=self.csrf())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.delete(f'{url}{mine.id}/', HTTP_X_CSRFTOKEN=self.csrf()).status_code, 400)
