"""Ownership, idempotency and quota. All mutations lock the owner before the conversation.

Provider calls happen between transactions. Expiring reservations fence late results,
including a process that returns after another request has reclaimed its reservation.
"""
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language

from .checks import SESSION_ENGINES
from .config import RESERVATION_SECONDS
from .rate_limits import reset_retry_after, retry_after_for
from .safety import ProviderError
from .models import ChatConversation, ChatMessage, ChatSessionUsage
from .providers import ChatTurn, get_provider
from .answering import ConversationAnswerer
from .grounding import AssistantReply
from .references import present_sources, reference_payload

logger = logging.getLogger(__name__)


class ChatError(Exception):
    def __init__(self, code, status, retry_after=None):
        self.retry_after = retry_after
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class Owner:
    user_id: int | None = None
    session_key: str = ''

    @property
    def filter(self):
        if self.user_id is not None:
            return {'user_id': self.user_id, 'anonymous_session_key': ''}
        return {'user__isnull': True, 'anonymous_session_key': self.session_key}

    @property
    def creation_fields(self):
        return {'user_id': self.user_id, 'anonymous_session_key': self.session_key}


def identify_owner(request):
    if request.user.is_authenticated:
        return Owner(user_id=request.user.pk)
    if settings.SESSION_ENGINE not in SESSION_ENGINES:
        raise ChatError('unsupported_session_engine', 503)
    # Force Django to validate/load the server session, discarding expired/unknown cookies.
    request.session.get('_chatbot_load')
    if request.session.session_key is None:
        request.session.create()
    return Owner(session_key=request.session.session_key)


@contextmanager
def owner_lock(owner, *, create_usage=True):
    with transaction.atomic():
        usage = None
        if owner.user_id is not None:
            get_user_model().objects.select_for_update().get(pk=owner.user_id)
        else:
            if create_usage:
                ChatSessionUsage.objects.get_or_create(anonymous_session_key=owner.session_key)
            usage = ChatSessionUsage.objects.select_for_update().get(anonymous_session_key=owner.session_key)
        yield usage


def expire_reservations(owner, usage):
    expired = ChatMessage.objects.filter(
        conversation__in=ChatConversation.objects.filter(**owner.filter),
        status=ChatMessage.Status.PENDING, reservation_expires_at__lte=timezone.now(),
    ).update(status=ChatMessage.Status.FAILED, reservation_expires_at=None)
    if expired and usage is not None:
        usage.pending_requests -= expired
        usage.save(update_fields=['pending_requests', 'updated_at'])
    return expired


def conversation_for(owner, conversation_id=None):
    conversations = ChatConversation.objects.filter(**owner.filter)
    if conversation_id is not None:
        conversation = conversations.filter(pk=conversation_id).first()
        if conversation is None:
            raise ChatError('conversation_not_found', 404)
        return conversation
    conversation = conversations.filter(status=ChatConversation.Status.ACTIVE).first()
    if conversation is None:
        retry_after = reset_retry_after(conversations)
        if retry_after is not None:
            raise ChatError('rate_limited', 429, retry_after=retry_after)
        conversation = ChatConversation.objects.create(**owner.creation_fields)
    return conversation


def snapshot(conversation, usage):
    limit = settings.CHATBOT_ANONYMOUS_LIMIT if usage is not None else None
    messages = list(conversation.messages.all())
    sources = present_sources(messages)
    return {
        'conversation': {'id': str(conversation.pk), 'status': conversation.status},
        'messages': [
            {'id': message.pk, 'role': message.role, 'content': message.content,
             'status': message.status, 'request_id': str(message.request_id),
             'created_at': message.created_at.isoformat(),
             **({'sources': sources[message.pk]} if message.role == 'assistant' else {})}
            for message in messages
        ],
        'usage': {
            'limit': limit,
            'consumed': usage.questions_consumed if usage is not None else None,
            'pending': usage.pending_requests if usage is not None else None,
            # Reservations reduce immediately available capacity without consuming it.
            'remaining': max(0, limit - usage.questions_consumed - usage.pending_requests)
            if usage is not None else None,
        },
    }


def get_session(owner, conversation_id=None):
    with owner_lock(owner) as usage:
        expire_reservations(owner, usage)
        return snapshot(conversation_for(owner, conversation_id), usage)


def reset_conversation(owner):
    with owner_lock(owner) as usage:
        expire_reservations(owner, usage)
        retry_after = reset_retry_after(ChatConversation.objects.filter(**owner.filter))
        if retry_after is not None:
            raise ChatError('rate_limited', 429, retry_after=retry_after)
        ChatConversation.objects.filter(**owner.filter, status='active').update(
            status='closed', updated_at=timezone.now())
        # Pending requests on the previous conversation retain their quota reservation.
        return snapshot(conversation_for(owner), usage)


def request_result(message, usage):
    status_code = {'pending': 202, 'completed': 200, 'failed': 502}[message.status]
    result = snapshot(message.conversation, usage)
    result.update(request_id=str(message.request_id), status=message.status)
    if message.status == ChatMessage.Status.FAILED:
        result['error'] = 'response_failed'
    return result, status_code


def reserve_message(owner, conversation_id, request_id, content):
    rejected = None
    with owner_lock(owner) as usage:
        expire_reservations(owner, usage)
        try:
            result = _reserve_locked(owner, usage, conversation_id, request_id, content)
        except ChatError as error:
            rejected = error
    if rejected is not None:
        raise rejected
    return result


def _reserve_locked(owner, usage, conversation_id, request_id, content):
    conversation = conversation_for(owner, conversation_id)
    existing = conversation.messages.filter(request_id=request_id, role='user').first()
    if existing is not None:
        if existing.content != content:
            raise ChatError('request_id_conflict', 409)
        return existing, False, request_result(existing, usage)
    if conversation.status != ChatConversation.Status.ACTIVE:
        raise ChatError('conversation_closed', 409)
    if conversation.messages.filter(status='pending').exists():
        raise ChatError('conversation_busy', 409)
    if usage is not None and usage.questions_consumed + usage.pending_requests >= settings.CHATBOT_ANONYMOUS_LIMIT:
        raise ChatError('quota_exhausted', 429)
    retry_after = retry_after_for(ChatConversation.objects.filter(**owner.filter))
    if retry_after is not None:
        raise ChatError('rate_limited', 429, retry_after=retry_after)
    message = conversation.messages.create(
        role='user', content=content, request_id=request_id, status='pending',
        reservation_expires_at=timezone.now() + timedelta(seconds=RESERVATION_SECONDS),
    )
    if usage is not None:
        usage.pending_requests += 1
        usage.save(update_fields=['pending_requests', 'updated_at'])
    ChatConversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
    return message, True, None


def finish_message(owner, message_id, answer):
    with owner_lock(owner) as usage:
        expire_reservations(owner, usage)
        message = ChatMessage.objects.select_related('conversation').filter(
            pk=message_id, conversation__in=ChatConversation.objects.filter(**owner.filter)).first()
        if message is None:
            raise ChatError('conversation_not_found', 404)
        if message.status != ChatMessage.Status.PENDING:
            return request_result(message, usage)
        if answer is not None:
            reply = answer if isinstance(answer, AssistantReply) else AssistantReply(answer)
            message.conversation.messages.create(role='assistant', content=reply.text,
                                                 sources=reference_payload(reply.references),
                                                 request_id=message.request_id, status='completed')
            message.status = ChatMessage.Status.COMPLETED
        else:
            message.status = ChatMessage.Status.FAILED
        message.reservation_expires_at = None
        message.save(update_fields=['status', 'reservation_expires_at'])
        if usage is not None:
            usage.pending_requests -= 1
            usage.questions_consumed += int(answer is not None)
            usage.save(update_fields=['pending_requests', 'questions_consumed', 'updated_at'])
        ChatConversation.objects.filter(pk=message.conversation_id).update(updated_at=timezone.now())
        return request_result(message, usage)


def validate_content(content):
    if not isinstance(content, str) or not content.strip() or '\x00' in content:
        raise ChatError('invalid_content', 400)
    if len(content) > settings.CHATBOT_MAX_INPUT_CHARS:
        raise ChatError('input_too_long', 400)
    try:
        content.encode('utf-8')
    except UnicodeEncodeError:
        raise ChatError('invalid_content', 400) from None


def send_message(owner, conversation_id, request_id, content, *, provider=None):
    validate_content(content)
    message, claimed, result = reserve_message(owner, conversation_id, request_id, content)
    if not claimed:
        return result
    try:
        previous = message.conversation.messages.filter(status='completed').order_by('-created_at', '-pk')
        limit = max(0, settings.CHATBOT_HISTORY_MESSAGES - 1)
        context_turns = settings.CHATBOT_CONTEXT_TURNS
        options = {}
        if settings.CHATBOT_GENERATION_MODE == 'full_faq':
            rows = list(reversed(previous[:min(context_turns * 2, limit)]))
            assistants = {row.request_id: row.content for row in rows if row.role == 'assistant'}
            recent, history = [], []
            for row in rows:
                if row.role == 'user':
                    recent.append(row.content)
                    history.append(ChatTurn('user', row.content))
                    if row.request_id in assistants:
                        history.append(ChatTurn('assistant', assistants[row.request_id]))
            options['history'] = history
        else:
            recent = [row.content for row in reversed(previous.filter(role='user')[:min(context_turns, limit)])]
        reply = ConversationAnswerer().answer_message(content, recent,
            interface_language=get_language() or settings.LANGUAGE_CODE,
            provider_factory=(lambda: provider) if provider is not None else get_provider, **options)
        answer = reply
        if not isinstance(reply.text, str) or not reply.text.strip():
            raise ValueError('Provider returned an empty or invalid response')
    except ProviderError as error:
        logger.warning('Chatbot provider failed reason=%s request=%s', error.code, message.request_id)
        answer = None
    except Exception:
        # Do not log private message content or provider exception details.
        logger.error('Chatbot provider failed for request %s', message.request_id)
        answer = None
    try:
        return finish_message(owner, message.pk, answer)
    except Exception:
        # A transient persistence failure should release the reservation when possible.
        # If the database is unavailable, expiry/cleanup will reclaim it on recovery.
        if answer is not None:
            finish_message(owner, message.pk, None)
        raise


def reap_expired_reservations():
    """Safe across multiple workers; uses the same lock order as HTTP requests."""
    owners = ChatConversation.objects.filter(messages__status='pending',
        messages__reservation_expires_at__lte=timezone.now()).values_list('user_id', 'anonymous_session_key').distinct()
    count = 0
    for user_id, session_key in list(owners):
        with owner_lock(Owner(user_id, session_key)) as usage:
            count += expire_reservations(Owner(user_id, session_key), usage)
    return count
