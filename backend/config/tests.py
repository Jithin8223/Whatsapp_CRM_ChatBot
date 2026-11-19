from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from config.health import dependency_checks


class HealthTests(SimpleTestCase):
    def test_liveness_is_public_and_minimal(self):
        response = self.client.get('/api/health/live/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    @patch('config.health.dependency_checks', return_value={'postgres': True, 'redis': True})
    def test_ready(self, checks):
        self.assertEqual(self.client.get('/api/health/ready/').status_code, 200)

    @patch('config.health.dependency_checks', return_value={'postgres': False, 'redis': True})
    def test_dependency_failure_is_503(self, checks):
        response = self.client.get('/api/health/ready/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('password', response.content.decode().lower())

    @patch('config.health.Redis.from_url', side_effect=ConnectionError('private credential'))
    @patch('config.health.connection')
    def test_probe_exceptions_are_sanitized(self, connection, redis):
        connection.cursor.side_effect = RuntimeError('private database credential')
        self.assertEqual(dependency_checks(), {'postgres': False, 'redis': False})

    @override_settings(DEBUG=False)
    def test_detailed_health_disabled_outside_development(self):
        self.assertEqual(self.client.get('/api/health/infrastructure/').status_code, 404)

    @override_settings(DEBUG=True)
    @patch('config.health.httpx.get', side_effect=__import__('httpx').ConnectError('unreachable'))
    @patch('config.health.dependency_checks', return_value={'postgres': True, 'redis': True})
    def test_connector_failure_is_reported(self, checks, request):
        response = self.client.get('/api/health/infrastructure/')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['checks']['wppconnect'])

    def test_health_rejects_posts(self):
        self.assertEqual(self.client.post('/api/health/live/').status_code, 405)
