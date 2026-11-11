"""Short atomic admissions + dedicated PostgreSQL session lock, never provider I/O in a tx."""
from contextlib import contextmanager
from hashlib import sha256

from django.db import connection, transaction
from django.utils import timezone

from .errors import EvaluationStopped
from .models import ChatEvaluationRun, ChatEvaluationAttempt
from .pricing import decimal, digest, money
from .token_budget import COUNTED_LIMIT, COUNTED_METHOD, COUNT_HEADROOM


@contextmanager
def execution_lock(run_id):
    if connection.vendor != 'postgresql':
        raise EvaluationStopped('postgresql_lock_required')
    # A dedicated session prevents reentrant same-connection advisory locks. No long tx.
    lock = connection.copy(alias='chatbot_evaluation_lock')
    key = int.from_bytes(sha256(str(run_id).encode()).digest()[:8], 'big', signed=True)
    acquired = False
    try:
        lock.ensure_connection()
        with lock.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [key])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise EvaluationStopped('execution_busy')
        yield
    finally:
        lock.close()  # Session loss releases lock; durable reservations are not released.


def open_run(run_id, configuration, *, resume=False):
    signature = digest(configuration)
    with transaction.atomic():
        run, created = ChatEvaluationRun.objects.get_or_create(pk=run_id, defaults={
            'configuration_hash': signature, 'configuration': configuration,
            'max_budget_usd': decimal(configuration['max_budget_usd'], positive=True),
            'max_requests': configuration['max_requests'],
        })
        run = ChatEvaluationRun.objects.select_for_update().get(pk=run.pk)
        if run.configuration_hash != signature:
            raise EvaluationStopped('configuration_changed')
        if not created and not resume:
            raise EvaluationStopped('execution_exists_use_resume')
        if not created:
            # Crash between intent and wire receipt is unknowable. Never resend that tuple.
            run.attempts.filter(state__in=['reserved', 'sent']).update(state='unknown', diagnostic='interrupted', updated_at=timezone.now())
            run.stop_reason = ''
            run.save(update_fields=['stop_reason', 'updated_at'])
    return run


def reserve(run_id, case_id, mode, *, tariff, input_ceiling, output_ceiling, payload_hash, source_hash,
            faq_version=None, measured_input_tokens=None):
    if type(output_ceiling) is not int or not 1 <= output_ceiling <= 400:
        raise EvaluationStopped('unreliable_cost_bound')
    reservation = tariff.cost(input_ceiling, output_ceiling, margin=True)
    with transaction.atomic():
        run = ChatEvaluationRun.objects.select_for_update().get(pk=run_id)
        config = run.configuration
        if config.get('token_bound_method') == COUNTED_METHOD:
            if (mode != 'full_faq' or type(measured_input_tokens) is not int
                    or not 0 < measured_input_tokens <= COUNTED_LIMIT - COUNT_HEADROOM
                    or input_ceiling != measured_input_tokens + COUNT_HEADROOM):
                raise EvaluationStopped('unreliable_cost_bound')
        elif input_ceiling != tariff.input_ceiling or measured_input_tokens is not None:
            raise EvaluationStopped('unreliable_cost_bound')
        if (tariff.model != config.get('model') or tariff.signature != config.get('tariff_hash')
                or input_ceiling > config.get('max_input_tokens', 0)
                or output_ceiling > config.get('max_output_tokens', 0)
                or mode not in config.get('modes', []) or case_id not in config.get('cases', [])):
            raise EvaluationStopped('request_outside_authorization')
        if run.attempts.filter(case_id=case_id, mode=mode).exists():
            raise EvaluationStopped('attempt_already_admitted')
        if run.stop_reason:
            raise EvaluationStopped(run.stop_reason)
        if run.admitted_count >= run.max_requests:
            raise EvaluationStopped('request_limit')
        if run.committed_usd + reservation > run.max_budget_usd:
            raise EvaluationStopped('budget_insufficient')
        if config.get('faq_version'):
            # Retrieval and the complete snapshot deliberately send different
            # evidence. Their shared database snapshot is pinned separately.
            if config['faq_version'] != faq_version:
                raise EvaluationStopped('source_versions_changed')
        else:
            other = run.attempts.filter(case_id=case_id).first()
            previous = [value for key, value in run.results.items() if key.startswith(case_id + ':')]
            if ((other is not None and other.source_hash != source_hash)
                    or any(value.get('input_source_hash', source_hash) != source_hash for value in previous)):
                raise EvaluationStopped('source_versions_changed')
        attempt = run.attempts.create(case_id=case_id, mode=mode, input_ceiling=input_ceiling,
            output_ceiling=output_ceiling, reserved_usd=reservation, charged_usd=reservation,
            payload_hash=payload_hash, source_hash=source_hash)
        run.admitted_count += 1
        run.committed_usd += reservation
        run.peak_committed_usd = max(run.peak_committed_usd, run.committed_usd)
        run.save(update_fields=['admitted_count', 'committed_usd', 'peak_committed_usd', 'updated_at'])
        return attempt


def mark_sent(attempt_id):
    with transaction.atomic():
        source = ChatEvaluationAttempt.objects.only('run_id').get(pk=attempt_id)
        run = ChatEvaluationRun.objects.select_for_update().get(pk=source.run_id)
        attempt = ChatEvaluationAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.state != 'reserved':
            raise EvaluationStopped('attempt_already_sent')
        attempt.state = 'sent'
        attempt.save(update_fields=['state', 'updated_at'])
        run.sent_count += 1  # Durable send intent, possibly an overcount if killed before wire.
        run.save(update_fields=['sent_count', 'updated_at'])


def known_usage(usage, model, tariff, attempt):
    if model not in tariff.response_models or not isinstance(usage, dict):
        raise EvaluationStopped('usage_unreliable')
    allowed = {'input_tokens', 'output_tokens', 'total_tokens', 'input_tokens_details', 'output_tokens_details'}
    if set(usage) - allowed:
        raise EvaluationStopped('unknown_usage_component')
    inputs, outputs = usage.get('input_tokens'), usage.get('output_tokens')
    if (type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0
            or inputs > attempt.input_ceiling or outputs > attempt.output_ceiling
            or usage.get('total_tokens') != inputs + outputs):
        raise EvaluationStopped('usage_outside_bound')
    for name, fields, total in [('input_tokens_details', {'cached_tokens', 'cache_write_tokens'}, inputs),
                                ('output_tokens_details', {'reasoning_tokens'}, outputs)]:
        details = usage.get(name) or {}
        optional = {'cache_write_tokens_details'} if name == 'input_tokens_details' and attempt.mode == 'full_faq' else set()
        if (not isinstance(details, dict) or set(details) - fields - optional
                or any(type(details.get(field, 0)) is not int or not 0 <= details.get(field, 0) <= total for field in fields)):
            raise EvaluationStopped('unknown_usage_component')
        if optional and (writes := details.get('cache_write_tokens_details')) is not None:
            if (not isinstance(writes, dict) or set(writes) - {'ttl_30m_tokens', 'ttl_12h_tokens'}
                    or any(type(count) is not int or count < 0 for count in writes.values())
                    or sum(writes.values()) != details.get('cache_write_tokens', 0)):
                raise EvaluationStopped('unknown_usage_component')
    # Full uncached rates + approved worst special rates: a usage-based ceiling, not invoice.
    return tariff.cost(inputs, outputs)


def settle(attempt_id, *, tariff, usage=None, model=None, completed=False, diagnostic='', receipt=None):
    with transaction.atomic():
        source = ChatEvaluationAttempt.objects.only('run_id').get(pk=attempt_id)
        run = ChatEvaluationRun.objects.select_for_update().get(pk=source.run_id)
        attempt = ChatEvaluationAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.state not in ('sent', 'reserved'):
            return attempt
        was_sent = attempt.state == 'sent'
        try:
            if not was_sent:
                raise EvaluationStopped('interrupted_before_send')
            charge = known_usage(usage, model, tariff, attempt)
            if charge > attempt.reserved_usd:
                raise EvaluationStopped('usage_outside_bound')
        except EvaluationStopped as error:
            attempt.state = 'unknown'
            attempt.diagnostic = error.code
            run.stop_reason = error.code
            # Preserve full reservation; do not persist untrusted provider error bodies.
        else:
            attempt.state = 'settled'
            attempt.usage = usage
            attempt.diagnostic = diagnostic
            run.committed_usd -= attempt.charged_usd - charge
            attempt.charged_usd = charge
        # A settled response can be incomplete: its usage is still accounted for.
        if completed and attempt.state == 'settled':
            run.completed_count += 1
        attempt.result = receipt or {}
        attempt.save(update_fields=['state', 'usage', 'diagnostic', 'charged_usd', 'result', 'updated_at'])
        run.save(update_fields=['committed_usd', 'completed_count', 'stop_reason', 'updated_at'])
        return attempt


def record_result(run_id, key, result):
    with transaction.atomic():
        run = ChatEvaluationRun.objects.select_for_update().get(pk=run_id)
        if key in run.results:
            return
        run.results[key] = result
        run.save(update_fields=['results', 'updated_at'])


def stop(run_id, reason):
    ChatEvaluationRun.objects.filter(pk=run_id).update(stop_reason=reason, updated_at=timezone.now())
