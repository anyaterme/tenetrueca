"""Independent evaluation ledger; never participates in public-chat quotas or moderation."""
from decimal import Decimal
import uuid

from django.db import models
from django.db.models import F, Q


class ChatEvaluationRun(models.Model):
    # This UUID must match the explicitly configured authorization, not a fresh CLI UUID.
    id = models.UUIDField(primary_key=True, editable=False)
    configuration_hash = models.CharField(max_length=64)
    configuration = models.JSONField(default=dict)
    max_budget_usd = models.DecimalField(max_digits=18, decimal_places=9)
    max_requests = models.PositiveSmallIntegerField()
    admitted_count = models.PositiveSmallIntegerField(default=0)
    sent_count = models.PositiveSmallIntegerField(default=0)
    completed_count = models.PositiveSmallIntegerField(default=0)
    committed_usd = models.DecimalField(max_digits=18, decimal_places=9, default=Decimal(0))
    peak_committed_usd = models.DecimalField(max_digits=18, decimal_places=9, default=Decimal(0))
    results = models.JSONField(default=dict)
    stop_reason = models.CharField(max_length=48, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(max_budget_usd__gt=0, max_budget_usd__lte=1), name='chat_eval_budget_cap'),
            models.CheckConstraint(condition=Q(max_requests__gt=0, max_requests__lte=20), name='chat_eval_request_cap'),
            models.CheckConstraint(condition=Q(committed_usd__gte=0, committed_usd__lte=F('max_budget_usd')), name='chat_eval_commit_cap'),
            models.CheckConstraint(condition=Q(peak_committed_usd__gte=F('committed_usd'), peak_committed_usd__lte=F('max_budget_usd')), name='chat_eval_peak_cap'),
            models.CheckConstraint(condition=Q(admitted_count__lte=F('max_requests'), sent_count__lte=F('admitted_count'),
                                              completed_count__lte=F('sent_count')), name='chat_eval_counts'),
        ]


class ChatEvaluationAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(ChatEvaluationRun, related_name='attempts', on_delete=models.PROTECT)
    case_id = models.CharField(max_length=16)
    mode = models.CharField(max_length=10)
    state = models.CharField(max_length=10, default='reserved')
    input_ceiling = models.PositiveIntegerField()
    output_ceiling = models.PositiveIntegerField()
    reserved_usd = models.DecimalField(max_digits=18, decimal_places=9)
    charged_usd = models.DecimalField(max_digits=18, decimal_places=9)
    usage = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    payload_hash = models.CharField(max_length=64)
    source_hash = models.CharField(max_length=64)
    diagnostic = models.CharField(max_length=48, default='', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['run', 'case_id', 'mode'], name='chat_eval_case_mode'),
            models.CheckConstraint(condition=Q(mode__in=['extractive', 'grounded', 'natural', 'full_faq']), name='chat_eval_mode'),
            models.CheckConstraint(condition=Q(state__in=['reserved', 'sent', 'settled', 'unknown']), name='chat_eval_state'),
            models.CheckConstraint(condition=Q(reserved_usd__gt=0, charged_usd__gte=0, charged_usd__lte=F('reserved_usd')),
                                   name='chat_eval_charge_cap'),
        ]
