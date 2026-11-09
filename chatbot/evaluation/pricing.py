"""Operator-approved USD ceilings, not a live pricing lookup or a tokenizer estimate."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

from .errors import EvaluationStopped

QUANTUM = Decimal('0.000000001')
# Reviewed official model capability, not a price. Whole context includes instructions,
# JSON schema, evidence, question and framing. No char/token ratio is used for admission.
MODEL_CEILINGS = {'gpt-6-luna': 1_050_000}
PRICE_SOURCE = 'https://developers.openai.com/api/docs/pricing'
MODEL_SOURCE = 'https://developers.openai.com/api/docs/models/'


def decimal(value, *, positive=False):
    try:
        if isinstance(value, (float, bool)):
            raise ValueError
        result = Decimal(value)
        if not result.is_finite() or result < 0 or positive and result <= 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise EvaluationStopped('invalid_money') from None


def money(value):
    return value.quantize(QUANTUM, rounding=ROUND_CEILING)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Tariff:
    model: str
    input_rate: Decimal
    output_rate: Decimal
    special_input_rate: Decimal
    fixed_rate: Decimal
    margin: Decimal
    input_ceiling: int
    response_models: tuple[str, ...]
    signature: str
    verified_on: str = ''

    def cost(self, input_tokens, output_tokens, *, margin=False):
        value = (Decimal(input_tokens) * (self.input_rate + self.special_input_rate)
                 + Decimal(output_tokens) * self.output_rate) / Decimal(1_000_000) + self.fixed_rate
        return money(value * (self.margin if margin else Decimal(1)))


def load_tariff(path, model, *, today=None, max_age_days=7):
    if not path:
        raise EvaluationStopped('pricing_missing')
    try:
        file = Path(path)
        if file.stat().st_size > 32768:
            raise ValueError
        data = json.loads(file.read_text())
    except (OSError, ValueError, RecursionError):
        raise EvaluationStopped('pricing_invalid') from None
    return validate_tariff(data, model, today=today, max_age_days=max_age_days)


def validate_tariff(data, model, *, today=None, max_age_days=7):
    today = today or date.today()
    if type(max_age_days) is not int or not 1 <= max_age_days <= 7:
        raise EvaluationStopped('pricing_validity_invalid')
    if not isinstance(data, dict) or data.get('approved') is not True:
        raise EvaluationStopped('pricing_unapproved')
    if model not in MODEL_CEILINGS or data.get('model') != model:
        raise EvaluationStopped('model_not_authorized')
    try:
        verified = date.fromisoformat(data['verified_on'])
        age = (today - verified).days
        if not 0 <= age <= max_age_days:
            raise EvaluationStopped('pricing_expired')
        if (data.get('source') != PRICE_SOURCE or data.get('model_source') != MODEL_SOURCE + model
                or data.get('currency') != 'USD' or data.get('service_tier') != 'default'
                or data.get('billing_coverage') != 'all_text_input_output_cache_writes_long_context_and_surcharges'
                or data.get('bound_strategy') != 'documented_full_context_ceiling'
                or data.get('rate_scope') != 'maximum_across_entire_context_window'
                or data.get('context_tokens') != MODEL_CEILINGS[model]
                or data.get('coverage_approved') is not True):
            raise EvaluationStopped('unreliable_cost_bound')
        rates = [decimal(data[key]) for key in ('input_usd_per_million', 'output_usd_per_million',
                                               'special_input_usd_per_million', 'fixed_request_usd')]
        margin = decimal(data['safety_margin'], positive=True)
        response_models = data['response_models']
        if (margin < Decimal('1.10') or margin > 10 or not rates[0] or not rates[1] or not rates[2]
                or not isinstance(response_models, list) or not response_models
                or any(not isinstance(m, str) or not m.startswith(model) for m in response_models)):
            raise EvaluationStopped('unreliable_cost_bound')
        return Tariff(model, *rates, margin, MODEL_CEILINGS[model], tuple(response_models), digest(data), data['verified_on'])
    except (KeyError, TypeError, ValueError):
        raise EvaluationStopped('pricing_invalid') from None
