"""Additional authorization can only restrict chatbot.safety; never bypass its guards."""
import sys
from uuid import UUID

from django.conf import settings
from django.db import connection

from chatbot import safety
from .errors import EvaluationStopped
from .pricing import decimal
from .pricing import QUANTUM


def limits(options):
    budget = decimal(options['max_budget_usd'], positive=True)
    ceiling = decimal(settings.CHATBOT_EVAL_MAX_BUDGET_USD, positive=True)
    if budget != budget.quantize(QUANTUM):
        raise EvaluationStopped('budget_precision')
    requests = options['max_requests']
    inputs, outputs = options['max_input_tokens'], options['max_output_tokens']
    if (not budget <= ceiling <= 1 or type(requests) is not int
            or not 1 <= requests <= settings.CHATBOT_EVAL_MAX_REQUESTS <= 20
            or type(inputs) is not int or not 1 <= inputs <= settings.CHATBOT_EVAL_MAX_INPUT_TOKENS
            or type(outputs) is not int or not 1 <= outputs <= settings.CHATBOT_EVAL_MAX_OUTPUT_TOKENS <= 400
            or options['model'] != settings.CHATBOT_OPENAI_MODEL):
        raise EvaluationStopped('authorization_limits')
    return budget


def require_evaluation_access(run_id):
    # The existing public chatbot guard also invokes the shared assistant test/CI detector.
    safety.require_openai_access()
    if (not settings.CHATBOT_EVAL_ALLOW_LIVE or settings.CHATBOT_EVAL_ENVIRONMENT != 'staging'
            or not settings.CHATBOT_EVAL_PROJECT_REF.startswith('proj_')
            or any(arg in ('runserver', 'gunicorn', 'uwsgi', 'migrate', 'makemigrations', 'collectstatic', 'check') for arg in sys.argv)):
        raise EvaluationStopped('live_evaluation_disabled')
    try:
        if UUID(settings.CHATBOT_EVAL_AUTHORIZATION_ID) != UUID(str(run_id)):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise EvaluationStopped('authorization_id_mismatch') from None
    if connection.vendor != 'postgresql':
        raise EvaluationStopped('postgresql_lock_required')
    if connection.in_atomic_block:
        raise EvaluationStopped('provider_transaction_forbidden')
    with connection.cursor() as cursor:
        cursor.execute('SHOW fsync')
        fsync = cursor.fetchone()[0]
        cursor.execute('SHOW synchronous_commit')
        synchronous_commit = cursor.fetchone()[0]
    if fsync != 'on' or synchronous_commit != 'on':
        raise EvaluationStopped('durable_database_required')
