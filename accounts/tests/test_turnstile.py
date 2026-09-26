import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from django.test import SimpleTestCase, override_settings

from accounts.services.turnstile import verify_turnstile


class UrlOpenResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


@override_settings(TURNSTILE_SECRET_KEY='secret', TURNSTILE_TIMEOUT=3)
class TurnstileServiceTests(SimpleTestCase):
    @patch('accounts.services.turnstile.urlopen')
    def test_valid_cloudflare_response_succeeds(self, urlopen):
        urlopen.return_value = UrlOpenResponse(json.dumps({'success': True}).encode())

        result = verify_turnstile('browser-token', remote_ip='203.0.113.10')

        self.assertTrue(result.valid)
        request = urlopen.call_args.args[0]
        self.assertIn(b'secret=secret', request.data)
        self.assertIn(b'response=browser-token', request.data)
        self.assertIn(b'remoteip=203.0.113.10', request.data)
        urlopen.assert_called_once_with(request, timeout=3)

    @patch('accounts.services.turnstile.urlopen')
    def test_invalid_cloudflare_response_fails_closed(self, urlopen):
        urlopen.return_value = UrlOpenResponse(json.dumps({'success': False}).encode())

        result = verify_turnstile('browser-token')

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, 'invalid')

    @patch('accounts.services.turnstile.urlopen', side_effect=URLError('offline'))
    def test_network_error_fails_closed(self, urlopen):
        result = verify_turnstile('browser-token')

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, 'unavailable')

    def test_missing_token_does_not_call_cloudflare(self):
        with patch('accounts.services.turnstile.urlopen') as urlopen:
            result = verify_turnstile('')

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, 'missing')
        urlopen.assert_not_called()

    @override_settings(TURNSTILE_SECRET_KEY='')
    def test_missing_secret_fails_closed(self):
        result = verify_turnstile('browser-token')

        self.assertFalse(result.valid)
        self.assertEqual(result.reason, 'configuration')
