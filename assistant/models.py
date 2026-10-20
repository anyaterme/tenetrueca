from django.conf import settings
from django.db import models
from django.utils import timezone


class PublicationEvaluation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        PROCESSING = 'processing', 'Procesando'
        COMPLETED = 'completed', 'Completada'
        ERROR = 'error', 'Error'
        OBSOLETE = 'obsolete', 'Obsoleta'

    publication = models.ForeignKey(
        'publications.Publication', on_delete=models.CASCADE, related_name='ai_evaluations',
    )
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    available_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    fingerprint = models.CharField(max_length=64, db_index=True)
    snapshot = models.JSONField(default=dict)
    model = models.CharField(max_length=100)
    rules_version = models.CharField(max_length=40)
    prompt_version = models.CharField(max_length=40)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=60, blank=True)
    error_details = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    is_simulated = models.BooleanField(default=True)
    is_manual = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['status', 'available_at'], name='ai_pending_jobs_idx')]
        constraints = [models.UniqueConstraint(
            fields=['publication'], condition=models.Q(status__in=['pending', 'processing']),
            name='ai_one_active_per_publication',
        )]


class OpenAIUsageDay(models.Model):
    """Serialize reservations across workers; failed/unknown calls keep their reservation."""
    date = models.DateField(unique=True)
    requests = models.PositiveIntegerField(default=0)
    reserved_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)


class EvaluationAttempt(models.Model):
    evaluation = models.ForeignKey(PublicationEvaluation, on_delete=models.CASCADE,
                                   related_name='usage_attempts')
    created_at = models.DateTimeField(default=timezone.now)
    response_id = models.CharField(max_length=120, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    reserved_usd = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    input_rate = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    output_rate = models.DecimalField(max_digits=14, decimal_places=8, default=0)
    error_code = models.CharField(max_length=60, blank=True)
    error_details = models.JSONField(default=dict, blank=True)
    is_simulated = models.BooleanField(default=True)
    usage_known = models.BooleanField(default=False)
