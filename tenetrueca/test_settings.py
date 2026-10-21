"""Isolated, offline test database. Never reads/writes the development database."""
from .settings import *

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
OPENAI_API_KEY = ''
OPENAI_LIVE_ENABLED = False
OPENAI_EVALUATION_BACKEND = 'mock'
# Keep mocked ledger checks independent of the local Docker zero-budget overrides.
# These limits never enable network access: tests and the SDK remain blocked.
OPENAI_DAILY_REQUEST_LIMIT = 20
OPENAI_DAILY_BUDGET_USD = Decimal('1')
OPENAI_TOTAL_BUDGET_USD = None
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
# Existing applications intentionally do not track migrations. Sync their models for tests.
MIGRATION_MODULES = {
    app.split('.apps.', 1)[0]: None
    for app in INSTALLED_APPS if not app.startswith(('django.', 'rest_framework'))
}
