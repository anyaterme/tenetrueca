"""Independent activation policy; reuse the existing test/CI detector without changing moderation."""
import sys

from django.conf import settings

from assistant.safety import is_test_process
from .config import provider_configuration_errors


class ProviderError(RuntimeError):
    """Only safe classification codes cross this boundary, never SDK error bodies."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require_openai_access():
    if is_test_process() or any(arg in ('migrate', 'makemigrations', 'collectstatic', 'check') for arg in sys.argv):
        raise ProviderError('live_calls_disabled')
    if not settings.CHATBOT_ENABLED or settings.CHATBOT_PROVIDER != 'openai':
        raise ProviderError('live_calls_disabled')
    if provider_configuration_errors() or not settings.OPENAI_API_KEY.strip():
        raise ProviderError('configuration')
