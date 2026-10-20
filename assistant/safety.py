"""Fail closed in tests/CI, independently of API keys and live settings."""
import functools
import os
import sys


_test_mode = False


def enable_test_mode():
    global _test_mode
    _test_mode = True


def is_test_process():
    return bool(
        _test_mode or 'pytest' in sys.modules or os.getenv('PYTEST_CURRENT_TEST')
        or os.getenv('CI', '').lower() not in ('', '0', 'false', 'no')
        or any(arg == 'test' or 'pytest' in arg or 'unittest' in arg for arg in sys.argv)
    )


class LiveCallsDisabled(RuntimeError):
    pass


def require_live_access():
    from django.conf import settings

    if is_test_process():
        raise LiveCallsDisabled('Las llamadas reales a OpenAI están bloqueadas en pruebas y CI.')
    if not settings.OPENAI_LIVE_ENABLED or not settings.OPENAI_API_KEY:
        raise LiveCallsDisabled('OpenAI real requiere activación explícita y una clave de servidor.')


def install_sdk_test_guard():
    # Covers direct SDK use too, including new clients and async clients outside our service.
    from openai._base_client import AsyncAPIClient, SyncAPIClient

    if getattr(SyncAPIClient.request, '_trueca_guard', False):
        return
    sync_request = SyncAPIClient.request
    async_request = AsyncAPIClient.request

    @functools.wraps(sync_request)
    def guarded_sync(self, *args, **kwargs):
        if is_test_process():
            raise LiveCallsDisabled('El SDK OpenAI no puede acceder a la red en pruebas/CI.')
        return sync_request(self, *args, **kwargs)

    @functools.wraps(async_request)
    async def guarded_async(self, *args, **kwargs):
        if is_test_process():
            raise LiveCallsDisabled('El SDK OpenAI no puede acceder a la red en pruebas/CI.')
        return await async_request(self, *args, **kwargs)

    guarded_sync._trueca_guard = True
    SyncAPIClient.request = guarded_sync
    AsyncAPIClient.request = guarded_async
