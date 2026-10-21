"""Offline SQLite tests with the existing on-disk migration artifacts enabled.

Requires the project's local base migrations, as does test_postgres_settings.
Never uses the development database or the live AI provider.
"""
from .test_settings import *

MIGRATION_MODULES = {}
