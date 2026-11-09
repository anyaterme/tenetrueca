"""History retention. Explicit command only: no startup hooks or data migrations.

Quota metadata is never removed while its Django Session row exists. All destructive
batches use the same owner lock as sends/resets/finalization, outside provider I/O.
"""
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import ChatConversation, ChatMessage, ChatSessionUsage
from .services import Owner, expire_reservations, owner_lock


def _eligible(owner, cutoff):
    conversations = ChatConversation.objects.filter(**owner.filter)
    pending = ChatMessage.objects.filter(conversation__in=conversations, status='pending').values('conversation_id')
    newer_in_turn = ChatMessage.objects.filter(conversation_id=OuterRef('conversation_id'),
        request_id=OuterRef('request_id'), created_at__gte=cutoff)
    messages = ChatMessage.objects.filter(conversation__in=conversations, created_at__lt=cutoff).exclude(
        conversation_id__in=pending).annotate(newer_in_turn=Exists(newer_in_turn)).filter(newer_in_turn=False)
    # Includes conversations that will become empty after eligible old turns are removed.
    recent = ChatMessage.objects.filter(conversation_id=OuterRef('pk'), created_at__gte=cutoff)
    empty_after = conversations.filter(updated_at__lt=cutoff).exclude(pk__in=pending).annotate(
        has_recent=Exists(recent)).filter(has_recent=False)
    return messages, empty_after


def cleanup_history(*, execute=False, batch_size=500):
    days = settings.CHATBOT_RETENTION_DAYS
    if type(days) is not int or days < 1 or not 1 <= batch_size <= 500:
        raise ValueError('Retention must be positive and batch_size must be between 1 and 500.')
    if settings.CHATBOT_RATE_LIMIT_WINDOW_SECONDS > days * 86400:
        raise ValueError('Retention must cover the rate limit window.')
    cutoff = timezone.now() - timedelta(days=days)
    result = {'dry_run': not execute, 'retention_days': days, 'messages': 0,
              'conversations': 0, 'usage_rows': 0, 'expired_reservations': 0, 'skipped_owners': 0}
    owners = ChatConversation.objects.order_by().values_list('user_id', 'anonymous_session_key').distinct()
    for user_id, session_key in owners.iterator(chunk_size=500):
        owner = Owner(user_id, session_key)
        if not execute:
            messages, conversations = _eligible(owner, cutoff)
            result['messages'] += messages.count()
            result['conversations'] += conversations.count()
            continue
        # A user may be deleted while a scheduled batch is selecting candidates.
        if user_id is not None and not get_user_model().objects.filter(pk=user_id).exists():
            continue
        try:
            while True:
                with owner_lock(owner, create_usage=False) as usage:
                    result['expired_reservations'] += expire_reservations(owner, usage)
                    messages, _ = _eligible(owner, cutoff)
                    turns = list(messages.order_by('conversation_id', 'request_id').values_list(
                        'conversation_id', 'request_id').distinct()[:batch_size])
                    groups = defaultdict(list)
                    for conversation_id, request_id in turns:
                        groups[conversation_id].append(request_id)
                    removed_messages = 0
                    if groups:
                        condition = Q(pk__in=[])
                        for conversation_id, identifiers in groups.items():
                            condition |= Q(conversation_id=conversation_id, request_id__in=identifiers)
                        removed = ChatMessage.objects.filter(condition).delete()[1]
                        removed_messages = removed.get('chatbot.ChatMessage', 0)
                        result['messages'] += removed_messages
                    empty = ChatConversation.objects.filter(**owner.filter, updated_at__lt=cutoff,
                                                             messages__isnull=True)
                    identifiers = list(empty.values_list('pk', flat=True)[:batch_size])
                    if identifiers:
                        ChatConversation.objects.filter(pk__in=identifiers).delete()
                        result['conversations'] += len(identifiers)
                if not turns and not identifiers:
                    break
        except (get_user_model().DoesNotExist, ChatSessionUsage.DoesNotExist):
            result['skipped_owners'] += 1

    # Keep keys/counts for all extant Django sessions, including rows awaiting clearsessions.
    # This prevents history cleanup from restoring quota or enabling cookie replay.
    stale_usage = ChatSessionUsage.objects.filter(updated_at__lt=cutoff, pending_requests=0).exclude(
        anonymous_session_key__in=Session.objects.values('session_key')).exclude(
        anonymous_session_key__in=ChatConversation.objects.values('anonymous_session_key'))
    if not execute:
        result['usage_rows'] = stale_usage.count()
    else:
        for key in stale_usage.values_list('anonymous_session_key', flat=True).iterator(chunk_size=500):
            try:
                with owner_lock(Owner(session_key=key), create_usage=False) as usage:
                    # Recheck under lock: HTTP/another cleaner may have changed ownership state.
                    if (usage.updated_at < cutoff and usage.pending_requests == 0
                            and not Session.objects.filter(session_key=key).exists()
                            and not ChatConversation.objects.filter(anonymous_session_key=key).exists()):
                        usage.delete()
                        result['usage_rows'] += 1
            except ChatSessionUsage.DoesNotExist:
                pass  # Another cleaner already removed this expired owner.
    return result
