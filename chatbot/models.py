import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ChatConversation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Activa'
        CLOSED = 'closed', 'Cerrada'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    anonymous_session_key = models.CharField(max_length=40, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(Q(user__isnull=False, anonymous_session_key='') |
                                              (Q(user__isnull=True) & ~Q(anonymous_session_key=''))),
                                   name='chat_conversation_owner'),
            models.CheckConstraint(condition=Q(status__in=['active', 'closed']), name='chat_conversation_status'),
            models.UniqueConstraint(fields=['user'], condition=Q(status='active', user__isnull=False),
                                    name='chat_active_user'),
            models.UniqueConstraint(fields=['anonymous_session_key'], condition=Q(status='active', user__isnull=True),
                                    name='chat_active_session'),
        ]
        indexes = [models.Index(fields=['anonymous_session_key', '-updated_at'], name='chat_session_history')]


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = 'user', 'Usuario'
        ASSISTANT = 'assistant', 'Asistente'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        COMPLETED = 'completed', 'Completada'
        FAILED = 'failed', 'Fallida'

    conversation = models.ForeignKey(ChatConversation, related_name='messages', on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    request_id = models.UUIDField()
    reservation_expires_at = models.DateTimeField(null=True, blank=True)
    # Public provenance only. Existing messages retain their original text and no sources.
    sources = models.JSONField(default=list, db_default=[], blank=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'request_id', 'role'], name='chat_request_role'),
            models.UniqueConstraint(fields=['conversation'], condition=Q(status='pending'), name='chat_one_pending'),
            models.CheckConstraint(condition=Q(role__in=['user', 'assistant']), name='chat_message_role'),
            models.CheckConstraint(condition=Q(status__in=['pending', 'completed', 'failed']), name='chat_message_status'),
            models.CheckConstraint(condition=Q(role='user') | Q(status='completed'), name='chat_assistant_completed'),
            models.CheckConstraint(condition=(Q(status='pending', reservation_expires_at__isnull=False) |
                                              (~Q(status='pending') & Q(reservation_expires_at__isnull=True))),
                                   name='chat_pending_expiry'),
        ]
        indexes = [
            models.Index(fields=['conversation', 'created_at', 'id'], name='chat_message_history'),
            models.Index(fields=['status', 'reservation_expires_at'], name='chat_reservation_expiry'),
        ]


class ChatSessionUsage(models.Model):
    anonymous_session_key = models.CharField(max_length=40, unique=True)
    questions_consumed = models.PositiveIntegerField(default=0)
    pending_requests = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(anonymous_session_key=''), name='chat_usage_session')]

# Discover the independent operator-only ledger without changing public conversation models.
from .evaluation.models import ChatEvaluationRun, ChatEvaluationAttempt  # noqa: E402,F401
