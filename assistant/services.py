import json
import logging
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Case, DecimalField, F, Sum, When
from django.utils import timezone
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import ValidationError as SchemaError

from assistant.errors import ERROR_MESSAGES, ResponseFailure, check_response, safe_error_details
from assistant.images import evaluation_photos
from assistant.models import EvaluationAttempt, OpenAIUsageDay, PublicationEvaluation
from assistant.providers import get_provider
from assistant.rules import ADMISSION_RULES, PROMPT_VERSION, RULES_VERSION, SYSTEM_INSTRUCTIONS
from assistant.safety import LiveCallsDisabled
from assistant.schema import CHECK_LABELS, OVERALL_LABELS, CheckStatus, EvaluationResult, response_json_schema
from assistant.snapshots import publication_snapshot
from audit.models import AuditEvent
from catalog.models import public_category_ids
from core.permissions import scope_publications, user_can_moderate
from publications.models import Publication


logger = logging.getLogger(__name__)


def schedule_evaluation(publication_id):
    # A failed AI queue must never roll back a citizen submission or block manual review.
    transaction.on_commit(lambda: enqueue_evaluation(publication_id), robust=True)


@transaction.atomic
def enqueue_evaluation(publication_id, *, requested_by=None, force=False, manual=False):
    publication = Publication.objects.select_for_update().get(pk=publication_id)
    if requested_by is not None:
        if not user_can_moderate(requested_by) or not scope_publications(
            requested_by, Publication.objects.filter(pk=publication_id)
        ).exists():
            raise PermissionDenied
    if publication.status != Publication.Status.PENDING_REVIEW:
        raise ValidationError('Solo se analizan publicaciones pendientes de revisión.')
    fingerprint, snapshot = publication_snapshot(publication)
    active = publication.ai_evaluations.filter(status__in=['pending', 'processing']).first()
    if active and active.fingerprint == fingerprint:
        return active, False
    if active:
        active.status = PublicationEvaluation.Status.OBSOLETE
        active.finished_at = timezone.now()
        active.save(update_fields=['status', 'finished_at'])
    latest = publication.ai_evaluations.first()
    if not force and latest and latest.fingerprint == fingerprint:
        return latest, False
    # Prevent accidental double-clicks/reloads from charging repeated manual evaluations.
    if force and latest and latest.fingerprint == fingerprint and (
        timezone.now() - latest.created_at < timedelta(seconds=60)
    ):
        raise ValidationError('Espera un minuto antes de repetir este análisis.')
    evaluation = PublicationEvaluation.objects.create(
        publication=publication, requested_by=requested_by, fingerprint=fingerprint,
        snapshot=snapshot, model=settings.OPENAI_MODEL or 'not-configured',
        rules_version=RULES_VERSION, prompt_version=PROMPT_VERSION, is_manual=manual,
    )
    AuditEvent.objects.create(
        actor=requested_by, action='publication.ai.queued', entity='Publication',
        entity_id=str(publication_id), source='assistant', metadata={'evaluation_id': evaluation.pk},
    )
    return evaluation, True


def current_evaluation(publication):
    latest = publication.ai_evaluations.first()
    if latest is None:
        return None
    fingerprint, _ = publication_snapshot(publication)
    if latest.fingerprint != fingerprint or latest.status == PublicationEvaluation.Status.OBSOLETE:
        return None
    return latest


def evaluation_context(publication):
    evaluation = current_evaluation(publication)
    checks = []
    categories = []
    if evaluation and evaluation.result:
        selection = evaluation.snapshot.get('classification', {})
        selected_labels = {
            'category': (selection.get('category') or {}).get('name', ''),
            'subcategory': (' / '.join(item['name'] for item in selection['path'])
                            if selection.get('subcategory') else
                            'Sin seleccionar' if selection.get('requires_subcategory')
                            else 'No aplica'),
        } if selection else {}
        checks = [{'label': label, 'selection': selected_labels.get(key, ''),
                   **evaluation.result['checks'][key]} for key, label in CHECK_LABELS.items()]
        alternatives = set(evaluation.result['alternative_category_ids'])
        categories = [category['name'] for category in evaluation.snapshot['categories']
                      if category['id'] in alternatives]
    return {
        'ai_evaluation': evaluation,
        'ai_checks': checks,
        'ai_alternative_categories': categories,
        'ai_overall': OVERALL_LABELS.get(evaluation.result.get('overall')) if evaluation else None,
        'ai_history': publication.ai_evaluations.all()[:20],
        'ai_can_repeat': publication.status == Publication.Status.PENDING_REVIEW,
        'ai_backend': settings.OPENAI_EVALUATION_BACKEND,
        'ai_error_message': ERROR_MESSAGES.get(evaluation.error_code, '') if evaluation else '',
    }


@transaction.atomic
def claim_evaluation(evaluation_id, *, manual=False):
    candidate = PublicationEvaluation.objects.get(pk=evaluation_id)
    if candidate.origin != 'review' or candidate.publication_id is None:
        return None
    # Same lock order as enqueue and moderation: publication, then evaluation.
    publication = Publication.objects.select_for_update().get(pk=candidate.publication_id)
    evaluation = PublicationEvaluation.objects.select_for_update().get(pk=evaluation_id)
    if (evaluation.status != 'pending' or evaluation.available_at > timezone.now()
            or evaluation.is_manual != manual or evaluation.origin != 'review'):
        return None
    fingerprint, _ = publication_snapshot(publication)
    if fingerprint != evaluation.fingerprint or publication.status != 'pending_review':
        evaluation.status = 'obsolete'
        evaluation.finished_at = timezone.now()
        evaluation.save(update_fields=['status', 'finished_at'])
        return None
    evaluation.status = 'processing'
    evaluation.started_at = timezone.now()
    evaluation.attempts += 1
    evaluation.save(update_fields=['status', 'started_at', 'attempts'])
    return evaluation


class BudgetExceeded(RuntimeError):
    pass


def check_request_reservation(evaluation, photo_count, output_tokens):
    # UTF-8 byte count intentionally overestimates text tokens; image allowance must be
    # reviewed for the explicitly selected model before activating real calls.
    input_allowance = (len(json.dumps(evaluation.snapshot, ensure_ascii=False).encode())
                       + len(SYSTEM_INSTRUCTIONS.encode())
                       + len(json.dumps(response_json_schema()).encode())
                       + 2048 + photo_count * max(8192, settings.OPENAI_IMAGE_TOKEN_RESERVE))
    estimate = (Decimal(input_allowance) * settings.OPENAI_INPUT_USD_PER_MILLION
                + Decimal(output_tokens) * settings.OPENAI_OUTPUT_USD_PER_MILLION) / Decimal(1_000_000)
    if not estimate.is_finite() or estimate > settings.OPENAI_REQUEST_RESERVE_USD:
        raise BudgetExceeded('La reserva es insuficiente para la entrada y salida máximas previstas.')


@transaction.atomic
def reserve_attempt(evaluation, *, simulated, budget=None):
    reservation = Decimal('0')
    if not simulated:
        if not settings.OPENAI_MODEL or evaluation.model != settings.OPENAI_MODEL:
            raise LiveCallsDisabled('El modelo debe configurarse explícitamente.')
        rates = (settings.OPENAI_INPUT_USD_PER_MILLION, settings.OPENAI_OUTPUT_USD_PER_MILLION)
        if any(not rate.is_finite() or rate <= 0 for rate in rates):
            raise LiveCallsDisabled('Configura las tarifas del modelo para registrar el coste.')
        reservation = settings.OPENAI_REQUEST_RESERVE_USD
        if not reservation.is_finite() or reservation <= 0:
            raise BudgetExceeded('La reserva debe ser positiva.')
        if budget is not None and reservation > budget:
            raise BudgetExceeded('La reserva por solicitud supera el presupuesto autorizado.')
        total_budget = settings.OPENAI_TOTAL_BUDGET_USD
        if total_budget is not None:
            if not total_budget.is_finite() or total_budget <= 0:
                raise BudgetExceeded('El presupuesto acumulado debe ser positivo.')
            if connection.vendor != 'postgresql':
                raise BudgetExceeded('El límite acumulado requiere PostgreSQL para reservar con seguridad.')
            # One transaction-scoped lock for all dates, shared by HTTP and workers.
            # A daily row lock alone cannot serialize requests across midnight.
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [1414677313, 1])
            committed = EvaluationAttempt.objects.filter(is_simulated=False).aggregate(
                total=Sum(Case(
                    When(usage_known=True, then=F('estimated_cost_usd')),
                    default=F('reserved_usd'),
                    output_field=DecimalField(max_digits=20, decimal_places=8),
                ), default=Decimal('0')),
            )['total']
            if committed + reservation > total_budget:
                raise BudgetExceeded('Límite acumulado de presupuesto alcanzado.')
        day, _ = OpenAIUsageDay.objects.get_or_create(date=timezone.now().date())
        day = OpenAIUsageDay.objects.select_for_update().get(pk=day.pk)
        if (day.requests >= settings.OPENAI_DAILY_REQUEST_LIMIT
                or day.reserved_usd + reservation > settings.OPENAI_DAILY_BUDGET_USD):
            raise BudgetExceeded('Límite diario de solicitudes o presupuesto alcanzado.')
        day.requests += 1
        day.reserved_usd += reservation
        day.save(update_fields=['requests', 'reserved_usd'])
    return EvaluationAttempt.objects.create(
        evaluation=evaluation, is_simulated=simulated, reserved_usd=reservation,
        input_rate=settings.OPENAI_INPUT_USD_PER_MILLION if not simulated else 0,
        output_rate=settings.OPENAI_OUTPUT_USD_PER_MILLION if not simulated else 0,
    )


@transaction.atomic
def record_usage(attempt, response):
    attempt.input_tokens = response.input_tokens
    attempt.output_tokens = response.output_tokens
    attempt.response_id = response.response_id
    attempt.usage_known = response.usage_known
    attempt.estimated_cost_usd = (
        Decimal(response.input_tokens) * attempt.input_rate
        + Decimal(response.output_tokens) * attempt.output_rate
    ) / Decimal(1_000_000)
    attempt.save()
    PublicationEvaluation.objects.filter(pk=attempt.evaluation_id).update(
        input_tokens=F('input_tokens') + response.input_tokens,
        output_tokens=F('output_tokens') + response.output_tokens,
        estimated_cost_usd=F('estimated_cost_usd') + attempt.estimated_cost_usd,
    )
    if not attempt.is_simulated and attempt.usage_known:
        day = OpenAIUsageDay.objects.select_for_update().get(date=attempt.created_at.date())
        day.reserved_usd = max(Decimal(0), day.reserved_usd - attempt.reserved_usd
                               + attempt.estimated_cost_usd)
        day.save(update_fields=['reserved_usd'])


def validate_result(payload, evaluation):
    result = EvaluationResult.model_validate_json(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    allowed_ids = {category['id'] for category in evaluation.snapshot['categories']}
    currently_allowed_ids = set(public_category_ids())
    if not set(result.alternative_category_ids).issubset(allowed_ids & currently_allowed_ids):
        raise ResponseFailure('invalid_response', stage='business', rule='category_not_allowed')
    for suggestion in result.suggestions:
        if suggestion.field == 'category' and (
            suggestion.category_id not in allowed_ids & currently_allowed_ids
            or any(category['parent_id'] == suggestion.category_id
                   for category in evaluation.snapshot['categories'])
        ):
            raise ResponseFailure('invalid_response', stage='business', rule='category_not_allowed')
    selection = evaluation.snapshot.get('classification')
    if selection:
        root = selection['category']
        subcategory = selection['subcategory']
        if not root or root['id'] not in currently_allowed_ids:
            result.checks.category.status = CheckStatus.ISSUE
            result.checks.category.explanation = 'La categoría raíz no está disponible en el catálogo.'
        if subcategory and subcategory['id'] not in currently_allowed_ids:
            result.checks.subcategory.status = CheckStatus.ISSUE
            result.checks.subcategory.explanation = (
                f'La subcategoría «{subcategory["name"]}» o su categoría superior no está activa.'
            )
        elif not subcategory and selection['requires_subcategory']:
            result.checks.subcategory.status = CheckStatus.ISSUE
            result.checks.subcategory.explanation = (
                f'Falta seleccionar una subcategoría dentro de «{root["name"]}».'
            )
        elif not subcategory and root and root['id'] in currently_allowed_ids:
            result.checks.subcategory.status = CheckStatus.CORRECT
            result.checks.subcategory.explanation = (
                f'No aplica: «{root["name"]}» no tiene subcategorías disponibles.'
            )
    dimensions = result.dimensions
    if dimensions.method == 'documented':
        # No verified technical lookup is integrated yet. A model-generated URL is not evidence.
        raise ResponseFailure('invalid_response', stage='business', rule='unverified_source')
    axes = [dimensions.height, dimensions.width, dimensions.depth]
    limit = ADMISSION_RULES['max_axis_cm']
    near_limit = limit - ADMISSION_RULES['verification_margin_cm']
    exceeds = any(a.minimum_cm is not None and a.minimum_cm > limit for a in axes)
    uncertain = dimensions.method in ('visual', 'unverifiable') or any(
        a.maximum_cm is None or a.maximum_cm >= near_limit for a in axes
    )
    if uncertain:
        dimensions.physical_verification = True
    if exceeds:
        result.checks.dimensions.status = CheckStatus.ISSUE
        result.checks.dimensions.explanation = f'Al menos un eje supera el límite de {limit} cm.'
        result.overall = 'issues'
        result.issues.append(result.checks.dimensions.explanation)
    elif uncertain:
        # Uncertainty must never erase an incident already reported by the model.
        if result.checks.dimensions.status != CheckStatus.ISSUE:
            result.checks.dimensions.status = CheckStatus.UNVERIFIABLE
            result.checks.dimensions.explanation = 'Las dimensiones requieren verificación física.'
    # overall is redundant with the checks and server-side dimensional rules. Resolve
    # contradictions conservatively, after validation, without losing useful checks.
    statuses = [check.status for check in result.checks.__dict__.values()]
    if CheckStatus.ISSUE in statuses or result.issues or result.overall == 'issues':
        result.overall = 'issues'
    elif (CheckStatus.UNVERIFIABLE in statuses or result.unverifiable
          or dimensions.physical_verification or result.overall == 'needs_verification'):
        result.overall = 'needs_verification'
    data = result.model_dump(mode='json')
    data['dimensions']['limit_cm'] = limit
    data['dimensions']['axis_comparison'] = [
        {'label': label, 'minimum_cm': axis.minimum_cm, 'maximum_cm': axis.maximum_cm,
         'status': ('NO_VERIFICABLE' if axis.minimum_cm is None else
                    'INCIDENCIA' if axis.minimum_cm > limit else
                    'NO_VERIFICABLE' if axis.maximum_cm >= near_limit else 'CORRECTO')}
        for label, axis in zip(('Alto', 'Ancho', 'Fondo'), axes)
    ]
    return data


def error_code(error):
    if isinstance(error, ResponseFailure):
        return error.code
    if isinstance(error, LiveCallsDisabled):
        return 'live_disabled'
    if isinstance(error, BudgetExceeded):
        return 'budget_exceeded'
    if isinstance(error, TimeoutError):
        return 'timeout_unknown_usage'
    if isinstance(error, APITimeoutError):
        return 'timeout_unknown_usage'
    if isinstance(error, APIConnectionError):
        return 'connection_unknown_usage'
    if isinstance(error, RateLimitError):
        return 'quota_exceeded' if error.code == 'insufficient_quota' else 'rate_limited'
    if isinstance(error, APIStatusError):
        if error.status_code >= 500:
            return 'server_error_unknown_usage'
        return {400: 'invalid_request', 401: 'authentication_error',
                403: 'permission_denied', 404: 'model_not_found'}.get(
                    error.status_code, 'provider_http_error')
    if isinstance(error, (SchemaError, ValueError, TypeError)):
        return 'invalid_response'
    return 'provider_error'


def evaluate_once(evaluation, photos, *, provider=None, budget=None, max_side=None,
                  max_output_tokens=None, timeout_seconds=None):
    """Shared analysis, validation and usage ledger for both submission stages."""
    attempt = None
    result = None
    code = ''
    details = {}
    started = time.monotonic()
    try:
        provider = provider or get_provider()
        if not photos:
            raise ValueError('No hay fotografías para analizar.')
        if not provider.is_simulated:
            check_request_reservation(evaluation, len(photos),
                                      max_output_tokens or settings.OPENAI_MAX_OUTPUT_TOKENS)
        attempt = reserve_attempt(evaluation, simulated=provider.is_simulated, budget=budget)
        evaluation.is_simulated = provider.is_simulated
        PublicationEvaluation.objects.filter(pk=evaluation.pk).update(
            is_simulated=provider.is_simulated,
        )
        options = {'max_side': max_side, 'max_output_tokens': max_output_tokens}
        if timeout_seconds is not None:
            options['timeout_seconds'] = timeout_seconds - (time.monotonic() - started)
            if options['timeout_seconds'] <= 0:
                raise TimeoutError
        response = provider.evaluate(evaluation, photos, **options)
        record_usage(attempt, response)
        check_response(response)
        result = validate_result(response.payload, evaluation)
    except Exception as error:
        # No raw exception/body/prompt/key/PII is persisted or sent to logs/templates.
        code = error_code(error)
        details = safe_error_details(error)
        logger.warning('Publication evaluation %s failed (%s)', evaluation.pk, code)
        if attempt:
            attempt.error_code = code
            attempt.error_details = details
            attempt.save(update_fields=['error_code', 'error_details'])
    return result, code, details


def process_evaluation(evaluation_id, *, provider=None, manual=False, budget=None,
                       max_side=None, max_output_tokens=None):
    candidate = PublicationEvaluation.objects.get(pk=evaluation_id)
    if candidate.origin != 'review' or candidate.publication_id is None:
        return False
    evaluation = claim_evaluation(evaluation_id, manual=manual)
    if evaluation is None:
        return False
    try:
        photos = evaluation_photos(evaluation.publication, manual=manual)
    except Exception as error:
        result, code, details = None, error_code(error), safe_error_details(error)
    else:
        result, code, details = evaluate_once(
            evaluation, photos, provider=provider, budget=budget, max_side=max_side,
            max_output_tokens=max_output_tokens,
        )
    with transaction.atomic():
        publication = Publication.objects.select_for_update().get(pk=evaluation.publication_id)
        latest = PublicationEvaluation.objects.select_for_update().get(pk=evaluation.pk)
        fingerprint, _ = publication_snapshot(publication)
        if latest.status != 'processing' or latest.fingerprint != fingerprint:
            # Preserve usage, but never publish a late/stale result as the current evaluation.
            if latest.status == 'processing':
                latest.status = 'obsolete'
                latest.finished_at = timezone.now()
                latest.save(update_fields=['status', 'finished_at'])
            return True
        latest.error_code = code
        latest.error_details = details
        if result is not None:
            latest.result = result
            latest.status = 'completed'
        elif (not manual and code == 'rate_limited'
              and latest.attempts < min(3, settings.OPENAI_MAX_ATTEMPTS)):
            latest.status = 'pending'
            latest.available_at = timezone.now() + timedelta(
                seconds=settings.OPENAI_RETRY_DELAY_SECONDS * latest.attempts,
            )
        else:
            latest.status = 'error'
        latest.finished_at = None if latest.status == 'pending' else timezone.now()
        latest.save(update_fields=['result', 'status', 'error_code', 'error_details',
                                   'available_at', 'finished_at'])
        AuditEvent.objects.create(
            action=f'publication.ai.{latest.status}', entity='Publication',
            entity_id=str(publication.pk), source='assistant',
            metadata={'evaluation_id': latest.pk, 'error_code': code,
                      'error_details': details,
                      'is_simulated': latest.is_simulated},
        )
    return True


def expire_interrupted_jobs():
    # Never retry a crashed/in-flight request: the provider may already have billed it.
    threshold = timezone.now() - timedelta(seconds=settings.OPENAI_TIMEOUT_SECONDS * 3 + 60)
    return PublicationEvaluation.objects.filter(origin='review', status='processing', started_at__lt=threshold).update(
        status='error', error_code='worker_interrupted_unknown_usage', finished_at=timezone.now(),
    )
