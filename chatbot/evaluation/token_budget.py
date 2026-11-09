"""Offline admission bound. Bytes are diagnostics, never a token conversion.

There is no reviewed local bound for Responses framing / Structured Outputs overhead.
An explicitly requested adjusted bound therefore fails closed; auto uses mechanism B.
The remote input-token endpoint is deliberately never called here.
"""
import json
from dataclasses import asdict, dataclass

import openai
from django.conf import settings

from .errors import EvaluationStopped
from .pricing import MODEL_CEILINGS, digest

REVIEWED_SDK = '2.54.0'
MAX_PAYLOAD_BYTES = 131072
BOUND_VERSION = 'responses-text-window-v1'


@dataclass(frozen=True)
class InputBound:
    method: str
    input_tokens: int
    output_tokens: int
    payload_hash: str
    payload_bytes: int
    component_bytes: dict
    adjusted_bound_available: bool = False
    fallback_reason: str = 'local_framing_and_schema_overhead_not_verified'
    version: str = BOUND_VERSION

    def report(self):
        return asdict(self)


def input_bound(arguments, *, max_input_tokens):
    method = settings.CHATBOT_EVAL_TOKEN_BOUND_METHOD
    if method not in ('conservative', 'auto', 'verified'):
        raise EvaluationStopped('unknown_token_bound_method')
    if method == 'verified':
        raise EvaluationStopped('verified_input_bound_unavailable')
    if openai.__version__ != REVIEWED_SDK:
        raise EvaluationStopped('unsupported_budget_sdk')
    required = {'model', 'instructions', 'input', 'text', 'max_output_tokens',
                'store', 'service_tier', 'truncation'}
    if (not isinstance(arguments, dict) or not required.issubset(arguments)
            or set(arguments) - required - {'reasoning'}):
        raise EvaluationStopped('unsupported_budget_payload')
    model = arguments['model']
    if model not in MODEL_CEILINGS:
        raise EvaluationStopped('model_not_authorized')
    ceiling = MODEL_CEILINGS[model]
    outputs = arguments['max_output_tokens']
    if (type(max_input_tokens) is not int or not ceiling <= max_input_tokens <= settings.CHATBOT_EVAL_MAX_INPUT_TOKENS
            or type(outputs) is not int or not 1 <= outputs <= min(settings.CHATBOT_EVAL_MAX_OUTPUT_TOKENS, 400)):
        raise EvaluationStopped('request_outside_token_bound')
    inputs, text = arguments['input'], arguments['text']
    if (arguments['store'] is not False or arguments['service_tier'] != 'default'
            or arguments['truncation'] != 'disabled' or not isinstance(arguments['instructions'], str)
            or not isinstance(inputs, list) or len(inputs) != 2
            or any(not isinstance(item, dict) or set(item) != {'role', 'content'}
                   or item['role'] != 'user' or not isinstance(item['content'], str) for item in inputs)
            or not isinstance(text, dict) or set(text) != {'format'}
            or not isinstance(text['format'], dict) or text['format'].get('type') != 'json_schema'
            or text['format'].get('strict') is not True or not isinstance(text['format'].get('schema'), dict)):
        raise EvaluationStopped('unsupported_budget_payload')
    try:
        def size(value):
            return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
        payload_bytes = size(arguments)
        components = {key: size(arguments[key]) for key in ('instructions', 'input', 'text')}
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise EvaluationStopped('unsupported_budget_payload') from None
    if payload_bytes > MAX_PAYLOAD_BYTES:
        raise EvaluationStopped('evaluation_payload_too_large')
    return InputBound('documented_full_context_ceiling', ceiling, outputs,
                      digest(arguments), payload_bytes, components)
