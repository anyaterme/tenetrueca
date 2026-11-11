"""Shared bounds; importing this module never creates clients or touches the network."""
from django.conf import settings

RESERVATION_SECONDS = 120


def provider_configuration_errors():
    errors = []
    if settings.CHATBOT_GENERATION_MODE not in ('natural', 'extractive', 'grounded', 'full_faq'):
        errors.append('CHATBOT_GENERATION_MODE must be natural, extractive, grounded or full_faq.')
    if settings.CHATBOT_PROVIDER not in ('mock', 'openai'):
        errors.append('CHATBOT_PROVIDER must be mock or openai.')
    if not isinstance(settings.CHATBOT_OPENAI_MODEL, str) or not settings.CHATBOT_OPENAI_MODEL.strip():
        errors.append('CHATBOT_OPENAI_MODEL must be nonempty.')
    timeout = settings.CHATBOT_REQUEST_TIMEOUT
    if type(timeout) not in (int, float) or not 0 < timeout < RESERVATION_SECONDS:
        errors.append('CHATBOT_REQUEST_TIMEOUT must be positive and below the reservation lease (120s).')
    for name in ('CHATBOT_HISTORY_MESSAGES', 'CHATBOT_CONTEXT_TURNS', 'CHATBOT_MAX_INPUT_CHARS', 'CHATBOT_MAX_OUTPUT_TOKENS', 'CHATBOT_RATE_LIMIT_REQUESTS', 'CHATBOT_RATE_LIMIT_WINDOW_SECONDS',
                 'CHATBOT_KNOWLEDGE_MAX_FRAGMENTS', 'CHATBOT_KNOWLEDGE_FRAGMENT_CHARS', 'CHATBOT_KNOWLEDGE_CONTEXT_CHARS',
                 'CHATBOT_FULL_FAQ_MAX_CHARS', 'CHATBOT_FULL_FAQ_HISTORY_CHARS', 'CHATBOT_FULL_FAQ_REQUEST_BYTES'):
        value = getattr(settings, name)
        if type(value) is not int or value < 1:
            errors.append(f'{name} must be a positive integer.')
    if settings.CHATBOT_REASONING_EFFORT not in ('', 'none', 'low', 'medium', 'high', 'xhigh', 'max'):
        errors.append('CHATBOT_REASONING_EFFORT is invalid.')
    return errors
