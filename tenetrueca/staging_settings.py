"""Isolated loopback staging; never imports local_settings or reads a dotenv file."""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

if os.environ.get('DJANGO_SETTINGS_MODULE') != __name__:
    raise ImproperlyConfigured('Select tenetrueca.staging_settings explicitly in a fresh process.')

from .settings import *  # noqa: E402,F403


def secret(name, minimum):
    try:
        value = (Path('/run/secrets') / name).read_text().strip()
    except OSError:
        raise ImproperlyConfigured(f'Missing independent staging secret: {name}.') from None
    if len(value) < minimum or any(c in value for c in ('\n', '\r', '\x00')):
        raise ImproperlyConfigured(f'Invalid staging secret: {name}.')
    return value


SECRET_KEY = secret('django_secret_key', 50)
EMAIL_SETTINGS_ENCRYPTION_KEY = SECRET_KEY
DEBUG = False
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql', 'NAME': 'trueca_chatbot_staging',
    'USER': 'trueca_staging', 'PASSWORD': secret('postgres_password', 32),
    'HOST': 'staging_db', 'PORT': '5432', 'CONN_MAX_AGE': 0,
    'OPTIONS': {'application_name': 'trueca_chatbot_staging', 'connect_timeout': 5},
}}
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_NAME = 'trueca_staging_sessionid'
CSRF_COOKIE_NAME = 'trueca_staging_csrftoken'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = 'Lax'
# This prepared profile is HTTP on loopback only. Remote HTTPS staging needs review.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = None
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
port = int(os.environ.get('TRUECA_STAGING_HTTP_PORT', '8083'))
if not 1024 <= port <= 65535 or port in (8000, 8082, 9000):
    raise ImproperlyConfigured('Use an independent staging loopback port.')
CSRF_TRUSTED_ORIGINS = [f'http://localhost:{port}', f'http://127.0.0.1:{port}']
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                      'LOCATION': 'trueca-chatbot-staging', 'KEY_PREFIX': 'trueca-staging'}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
GOOGLE_MAPS_API_KEY = GOOGLE_MAPS_MAP_ID = ''
AUTH_API_URL = AUTH_API_TOKEN = ''
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'media'
PRIVATE_MEDIA_ROOT = BASE_DIR / 'private_media'

# Only the separately mounted operator CLI may opt in after a new authorization.
pilot = (sys.argv[1:2] == ['evaluate_chatbot_live']
         and os.environ.get('TRUECA_STAGING_PILOT_AUTHORIZED') == 'explicit-operator-authorization')
requested_live = os.environ.get('CHATBOT_EVAL_ALLOW_LIVE', '').lower() in ('1', 'true', 'yes', 'on')
if (requested_live or os.environ.get('CHATBOT_PROVIDER', 'mock') != 'mock') and not pilot:
    raise ImproperlyConfigured('The staging web process cannot enable OpenAI.')
CHATBOT_PROVIDER = 'openai' if pilot and requested_live else 'mock'
CHATBOT_EVAL_ALLOW_LIVE = bool(pilot and requested_live)
CHATBOT_EVAL_ENVIRONMENT = 'staging'
CHATBOT_EVAL_MAX_BUDGET_USD = min(CHATBOT_EVAL_MAX_BUDGET_USD, Decimal('1.00'))
CHATBOT_EVAL_MAX_REQUESTS = min(CHATBOT_EVAL_MAX_REQUESTS, 20)
CHATBOT_EVAL_MAX_OUTPUT_TOKENS = min(CHATBOT_EVAL_MAX_OUTPUT_TOKENS, 400)
CHATBOT_MAX_OUTPUT_TOKENS = min(CHATBOT_MAX_OUTPUT_TOKENS, CHATBOT_EVAL_MAX_OUTPUT_TOKENS)
CHATBOT_REQUEST_TIMEOUT = min(CHATBOT_REQUEST_TIMEOUT, 30)
CHATBOT_EVAL_MAX_INPUT_TOKENS = min(CHATBOT_EVAL_MAX_INPUT_TOKENS, 1050000)
OPENAI_API_KEY = secret('openai_evaluation_key', 20) if CHATBOT_EVAL_ALLOW_LIVE else ''
OPENAI_LIVE_ENABLED = False
OPENAI_EVALUATION_BACKEND = 'disabled'
OPENAI_MODEL = ''
OPENAI_DAILY_REQUEST_LIMIT = 0
OPENAI_DAILY_BUDGET_USD = Decimal(0)
OPENAI_TOTAL_BUDGET_USD = Decimal(0)

