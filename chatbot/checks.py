from django.conf import settings
from django.core.checks import Error, Warning, register

from .config import provider_configuration_errors


SESSION_ENGINES = {'django.contrib.sessions.backends.db', 'django.contrib.sessions.backends.cached_db'}


@register()
def chatbot_configuration(app_configs, **kwargs):
    errors = []
    retention = settings.CHATBOT_RETENTION_DAYS
    if type(retention) is not int or retention < 1:
        errors.append(Error('CHATBOT_RETENTION_DAYS must be a positive integer.', id='chatbot.E004'))
    elif settings.CHATBOT_ENABLED and settings.CHATBOT_RATE_LIMIT_WINDOW_SECONDS > retention * 86400:
        errors.append(Error('Chatbot retention must cover the rate limit window.', id='chatbot.E004'))
    if not settings.CHATBOT_ENABLED:
        return errors
    if settings.SESSION_ENGINE not in SESSION_ENGINES:
        errors.append(Error('Chatbot requires database-backed Django sessions.', id='chatbot.E001'))
    for name in ('CHATBOT_ANONYMOUS_LIMIT', 'CHATBOT_MAX_INPUT_CHARS', 'CHATBOT_HISTORY_MESSAGES'):
        value = getattr(settings, name)
        if type(value) is not int or value < 1:
            errors.append(Error(f'{name} must be a positive integer.', id='chatbot.E002'))
    errors.extend(Error(message, id='chatbot.E003') for message in provider_configuration_errors())
    from .knowledge.registry import APPROVED_CLAIMS, BLOCKED_CLAIMS
    if APPROVED_CLAIMS.intersection(BLOCKED_CLAIMS):
        errors.append(Error('A blocked knowledge claim cannot be approved.', id='chatbot.E005'))
    if settings.CHATBOT_PROVIDER == 'openai' and not settings.OPENAI_API_KEY.strip():
        errors.append(Warning('Chatbot OpenAI is selected but OPENAI_API_KEY is missing; requests will fail closed.', id='chatbot.W001'))
    return errors
