"""Budget the documented full window or an authorized Responses token count."""
import json
from dataclasses import asdict, dataclass

import openai
from django.conf import settings

from chatbot.full_faq_protocol import CACHE_BREAKPOINT, CACHE_OPTIONS

from .errors import EvaluationStopped
from .pricing import MODEL_CEILINGS, digest

REVIEWED_SDK = '2.54.0'
MAX_PAYLOAD_BYTES = 131072
BOUND_VERSION = 'responses-text-window-v2'
COUNTED_METHOD = 'provider_counted'
COUNTED_LIMIT = 8192
COUNT_HEADROOM = 512


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
    measured_input_tokens: int | None = None

    def report(self):
        return asdict(self)


def supported_messages(inputs, cache):
    if (not isinstance(inputs, list) or len(inputs) != 2
            or any(not isinstance(item, dict) or set(item) != {'role', 'content'}
                   or item['role'] != 'user' for item in inputs)):
        return False
    first, second = (item['content'] for item in inputs)
    if not isinstance(second, str):
        return False
    if cache is None:
        return isinstance(first, str)
    if cache != CACHE_OPTIONS or not isinstance(first, list) or len(first) != 1:
        return False
    block = first[0]
    return (isinstance(block, dict) and set(block) == {'type', 'text', 'prompt_cache_breakpoint'}
            and block['type'] == 'input_text' and isinstance(block['text'], str)
            and block['prompt_cache_breakpoint'] == CACHE_BREAKPOINT)


def input_bound(arguments, *, max_input_tokens, measured_input_tokens=None):
    method = settings.CHATBOT_EVAL_TOKEN_BOUND_METHOD
    if method not in ('conservative', 'auto', 'verified', COUNTED_METHOD):
        raise EvaluationStopped('unknown_token_bound_method')
    if method == 'verified':
        raise EvaluationStopped('verified_input_bound_unavailable')
    if openai.__version__ != REVIEWED_SDK:
        raise EvaluationStopped('unsupported_budget_sdk')
    required = {'model', 'instructions', 'input', 'text', 'max_output_tokens',
                'store', 'service_tier', 'truncation'}
    if (not isinstance(arguments, dict) or not required.issubset(arguments)
            or set(arguments) - required - {'reasoning', 'prompt_cache_options'}):
        raise EvaluationStopped('unsupported_budget_payload')
    model = arguments['model']
    if model not in MODEL_CEILINGS:
        raise EvaluationStopped('model_not_authorized')
    ceiling = MODEL_CEILINGS[model]
    outputs = arguments['max_output_tokens']
    minimum = COUNT_HEADROOM + 1 if method == COUNTED_METHOD else ceiling
    if (type(max_input_tokens) is not int or not minimum <= max_input_tokens <= settings.CHATBOT_EVAL_MAX_INPUT_TOKENS
            or type(outputs) is not int or not 1 <= outputs <= min(settings.CHATBOT_EVAL_MAX_OUTPUT_TOKENS, 400)):
        raise EvaluationStopped('request_outside_token_bound')
    inputs, text = arguments['input'], arguments['text']
    if (arguments['store'] is not False or arguments['service_tier'] != 'default'
            or arguments['truncation'] != 'disabled' or not isinstance(arguments['instructions'], str)
            or not supported_messages(inputs, arguments.get('prompt_cache_options'))
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
    if method == COUNTED_METHOD:
        if arguments.get('prompt_cache_options') != CACHE_OPTIONS:
            raise EvaluationStopped('provider_count_requires_full_faq')
        cap = min(max_input_tokens, COUNTED_LIMIT)
        if measured_input_tokens is None:
            return InputBound('provider_count_required', cap, outputs, digest(arguments), payload_bytes,
                              components, fallback_reason='live_count_required')
        if (type(measured_input_tokens) is not int or not 0 < measured_input_tokens <= cap - COUNT_HEADROOM):
            raise EvaluationStopped('provider_count_outside_budget')
        return InputBound(COUNTED_METHOD, measured_input_tokens + COUNT_HEADROOM, outputs, digest(arguments),
                          payload_bytes, components, adjusted_bound_available=True, fallback_reason='',
                          measured_input_tokens=measured_input_tokens)
    return InputBound('documented_full_context_ceiling', ceiling, outputs,
                      digest(arguments), payload_bytes, components)
