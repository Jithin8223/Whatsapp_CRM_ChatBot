"""Production configuration: Supabase DATABASE_URL parsing, proxy/IP trust, caching, health."""
import time

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from config.database import database_settings
from config.health import WORKER_HEARTBEAT_KEY
from config.middleware import TrustedClientIPMiddleware

SUPABASE = 'postgresql://postgres.abcdefgh:p%40ss%2Fword@aws-0-eu-west-1.pooler.supabase.com:{port}/postgres'


class DatabaseSettingsTests(SimpleTestCase):
    def test_local_docker_settings_are_unchanged(self):
        config = database_settings({'POSTGRES_PASSWORD': 'local', 'POSTGRES_HOST': 'postgres'}, debug=True)
        self.assertEqual((config['HOST'], config['NAME'], config['CONN_MAX_AGE']), ('postgres', 'whatsapp_crm', 0))
        self.assertNotIn('sslmode', config['OPTIONS'])

    def test_supabase_session_pooler_uses_tls_and_decodes_credentials(self):
        config = database_settings({'DATABASE_URL': SUPABASE.format(port=5432), 'DB_CONN_MAX_AGE': '60'}, debug=False)
        self.assertEqual(config['HOST'], 'aws-0-eu-west-1.pooler.supabase.com')
        self.assertEqual((config['USER'], config['PASSWORD'], config['NAME']), ('postgres.abcdefgh', 'p@ss/word', 'postgres'))
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')
        self.assertEqual(config['OPTIONS']['connect_timeout'], 5)
        self.assertTrue(config['CONN_HEALTH_CHECKS'])
        self.assertFalse(config['DISABLE_SERVER_SIDE_CURSORS'])

    def test_transaction_pooler_disables_server_side_cursors(self):
        config = database_settings({'DATABASE_URL': SUPABASE.format(port=6543)}, debug=False)
        self.assertTrue(config['DISABLE_SERVER_SIDE_CURSORS'])
        self.assertEqual(config['PORT'], '6543')

    def test_production_refuses_unencrypted_or_malformed_urls(self):
        with self.assertRaises(ImproperlyConfigured):
            database_settings({'DATABASE_URL': SUPABASE.format(port=5432), 'DB_SSLMODE': 'disable'}, debug=False)
        with self.assertRaises(ImproperlyConfigured):
            database_settings({'DATABASE_URL': 'mysql://x@y/z'}, debug=False)
        with self.assertRaises(ImproperlyConfigured):
            database_settings({'DATABASE_URL': SUPABASE.format(port=5432), 'DB_CONN_MAX_AGE': 'forever'}, debug=False)
        verify = database_settings({'DATABASE_URL': SUPABASE.format(port=5432), 'DB_SSLMODE': 'verify-full',
                                    'DB_SSLROOTCERT': '/run/secrets/supabase-ca.crt'}, debug=False)
        self.assertEqual(verify['OPTIONS']['sslrootcert'], '/run/secrets/supabase-ca.crt')


@override_settings(TRUSTED_CLIENT_IP_HEADER='CF-Connecting-IP', TRUSTED_PROXY_NETWORKS=['172.16.0.0/12'])
class ProxyTrustTests(SimpleTestCase):
    def run_middleware(self, peer, header):
        request = RequestFactory().get('/api/x', REMOTE_ADDR=peer, HTTP_CF_CONNECTING_IP=header)
        TrustedClientIPMiddleware(lambda r: r)(request)
        return request.META['REMOTE_ADDR']

    def test_cloudflare_ip_trusted_only_from_internal_proxy(self):
        self.assertEqual(self.run_middleware('172.19.0.5', '203.0.113.9'), '203.0.113.9')
        self.assertEqual(self.run_middleware('198.51.100.7', '203.0.113.9'), '198.51.100.7')  # spoof attempt
        self.assertEqual(self.run_middleware('172.19.0.5', 'not-an-ip'), '172.19.0.5')


class ResponseSecurityTests(TestCase):
    def test_api_responses_are_private_and_uncacheable(self):
        response = APIClient().get('/api/health/live/')
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertIn('Cookie', response['Vary'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')

    def test_rate_limit_ignores_spoofed_forwarded_for(self):
        from rest_framework.throttling import AnonRateThrottle
        request = RequestFactory().get('/', REMOTE_ADDR='198.51.100.7', HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(AnonRateThrottle().get_ident(request), '198.51.100.7')


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'health'}})
class HealthStatusTests(TestCase):
    @override_settings(DEBUG=False, HEALTH_CHECK_TOKEN='monitor-token-123')
    def test_status_requires_token_and_reports_worker(self):
        from django.core.cache import cache
        client = APIClient()
        self.assertEqual(client.get('/api/health/status/').status_code, 404)
        self.assertEqual(client.get('/api/health/status/', HTTP_AUTHORIZATION='Bearer wrong').status_code, 404)
        cache.delete(WORKER_HEARTBEAT_KEY)
        report = client.get('/api/health/status/', HTTP_AUTHORIZATION='Bearer monitor-token-123')
        self.assertFalse(report.json()['checks']['celery_worker'])  # a running container is not proof of health
        cache.set(WORKER_HEARTBEAT_KEY, time.time())
        data = client.get('/api/health/status/', HTTP_AUTHORIZATION='Bearer monitor-token-123').json()
        self.assertTrue(data['checks']['celery_worker'])
        self.assertIn('webhook_backlog', data['metrics'])
        self.assertIn('whatsapp_sessions_connected', data['metrics'])
