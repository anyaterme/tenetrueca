"""Rolling rate limit over existing persisted attempts, shared by all web workers.

Caller MUST hold owner_lock. Failures count as attempts, but never as consumed quota.
No locmem cache: it would split the limit across Gunicorn processes.
"""
from datetime import timedelta
from math import ceil

from django.conf import settings
from django.utils import timezone

from .models import ChatMessage


def _retry_after(attempts):
    now = timezone.now()
    window = settings.CHATBOT_RATE_LIMIT_WINDOW_SECONDS
    attempts = list(attempts.filter(created_at__gt=now - timedelta(seconds=window)).order_by('-created_at', '-pk').values_list('created_at', flat=True)[:settings.CHATBOT_RATE_LIMIT_REQUESTS])
    if len(attempts) < settings.CHATBOT_RATE_LIMIT_REQUESTS:
        return None
    return max(1, ceil((attempts[-1] + timedelta(seconds=window) - now).total_seconds()))


def retry_after_for(conversations):
    return _retry_after(ChatMessage.objects.filter(conversation__in=conversations, role='user'))


def reset_retry_after(conversations):
    # Separate bucket: creating empty conversations must not bypass storage protection.
    return _retry_after(conversations)
