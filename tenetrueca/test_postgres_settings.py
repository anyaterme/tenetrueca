"""Optional isolated PostgreSQL checks; never inherits development DB credentials."""
import os

from .test_settings import *

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': os.getenv('OPENAI_TEST_DB_NAME', 'trueca_ai_test'),
    'USER': os.getenv('OPENAI_TEST_DB_USER', 'postgres'),
    'PASSWORD': os.getenv('OPENAI_TEST_DB_PASSWORD', 'temporary-test-password'),
    'HOST': '127.0.0.1',
    'PORT': os.getenv('OPENAI_TEST_DB_PORT', '55439'),
}}
MIGRATION_MODULES = {}
