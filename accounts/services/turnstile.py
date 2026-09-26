import json
import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)
TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


@dataclass(frozen=True)
class TurnstileVerification:
    valid: bool
    reason: str = ''


def verify_turnstile(token, remote_ip=None):
    token = (token or '').strip()
    if not token:
        return TurnstileVerification(False, 'missing')

    secret_key = str(getattr(settings, 'TURNSTILE_SECRET_KEY', '') or '')
    if not secret_key:
        logger.error('Turnstile no está configurado para el registro público.')
        return TurnstileVerification(False, 'configuration')

    payload = {'secret': secret_key, 'response': token}
    if remote_ip:
        payload['remoteip'] = remote_ip
    request = Request(
        TURNSTILE_VERIFY_URL,
        data=urlencode(payload).encode('ascii'),
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Tenetrueca/registration',
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=settings.TURNSTILE_TIMEOUT) as response:
            result = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning('No se pudo verificar Turnstile para un intento de registro.')
        return TurnstileVerification(False, 'unavailable')

    if not isinstance(result, dict):
        logger.warning('Turnstile devolvió una respuesta inesperada.')
        return TurnstileVerification(False, 'unavailable')
    if result.get('success') is True:
        return TurnstileVerification(True)
    return TurnstileVerification(False, 'invalid')
