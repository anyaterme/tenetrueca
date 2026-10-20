import base64
import json
from dataclasses import dataclass
from itertools import islice

from django.conf import settings

from assistant.images import image_options, optimize_image
from assistant.rules import ADMISSION_RULES, SYSTEM_INSTRUCTIONS
from assistant.safety import LiveCallsDisabled, require_live_access
from assistant.schema import response_json_schema


@dataclass
class ProviderResponse:
    payload: object
    input_tokens: int = 0
    output_tokens: int = 0
    response_id: str = ''
    usage_known: bool = True
    status: str = 'completed'
    incomplete_reason: str = ''
    refused: bool = False


def image_content(photo, *, max_side=None, detail=None):
    options = image_options(max_side=max_side, detail=detail)
    with optimize_image(photo, options=options) as output:
        return {'type': 'input_image', 'detail': options.detail,
                'image_url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()}


def request_content(evaluation, photos, *, max_side=None, detail=None):
    options = image_options(max_side=max_side, detail=detail)
    snapshot = evaluation.snapshot
    data = {key: snapshot[key] for key in (
        'title', 'description', 'category_id', 'classification', 'categories',
    )}
    data['admission_rules'] = ADMISSION_RULES
    text = json.dumps(data, ensure_ascii=False)
    if len(text.encode()) > settings.OPENAI_MAX_INPUT_BYTES:
        raise ValueError('La entrada excede el límite configurado.')
    return [{'type': 'input_text', 'text': text}] + [
        image_content(photo, max_side=max_side, detail=detail)
        for photo in islice(photos, options.max_count)
    ]


class OpenAIProvider:
    is_simulated = False

    def evaluate(self, evaluation, photos, *, max_side=None, max_output_tokens=None):
        require_live_access()
        from openai import OpenAI

        output_limit = max_output_tokens or settings.OPENAI_MAX_OUTPUT_TOKENS
        if not 256 <= output_limit <= 4096 or not 5 <= settings.OPENAI_TIMEOUT_SECONDS <= 120:
            raise ValueError('Límites de salida o timeout no válidos.')
        content = request_content(evaluation, photos, max_side=max_side,
                                  detail='low' if evaluation.is_manual else None)
        manual_options = {}
        if evaluation.is_manual and evaluation.model == 'gpt-6-luna':
            # Keep the explicitly authorized manual check on standard pricing, without
            # spending the small output allowance on internal reasoning.
            manual_options = {'reasoning': {'effort': 'none'}, 'service_tier': 'default'}
        # SDK retries are always disabled; only the durable queue may retry safe 429s.
        with OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=0,
                    timeout=settings.OPENAI_TIMEOUT_SECONDS,
                    base_url='https://api.openai.com/v1') as client:
            response = client.responses.create(
                model=evaluation.model, instructions=SYSTEM_INSTRUCTIONS,
                input=[{'role': 'user', 'content': content}],
                text={'format': {'type': 'json_schema', 'name': 'publication_evaluation',
                                 'schema': response_json_schema(), 'strict': True}},
                max_output_tokens=output_limit,
                store=False,
                **manual_options,
            )
        # Use create + local Pydantic validation to retain usage even on invalid/refused output.
        usage = response.usage
        refused = any(
            item.type == 'refusal'
            for output in response.output if output.type == 'message'
            for item in output.content
        )
        reason = response.incomplete_details.reason if response.incomplete_details else ''
        return ProviderResponse(
            payload=response.output_text,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            response_id=response.id,
            usage_known=usage is not None,
            status=response.status,
            incomplete_reason=reason if reason in ('max_output_tokens', 'content_filter') else '',
            refused=refused,
        )


class MockProvider:
    is_simulated = True

    def evaluate(self, evaluation, photos, **kwargs):
        from assistant.fixtures import simulated_response

        payload = simulated_response()
        selection = evaluation.snapshot.get('classification', {})
        for field in ('category', 'subcategory'):
            selected = selection.get(field)
            if selected:
                payload['checks'][field]['explanation'] = (
                    f'Simulación: comprobar manualmente si el objeto corresponde a «{selected["name"]}».'
                )
        return ProviderResponse(payload=payload)


def get_provider():
    if settings.OPENAI_EVALUATION_BACKEND == 'mock':
        return MockProvider()
    if settings.OPENAI_EVALUATION_BACKEND == 'live':
        require_live_access()
        return OpenAIProvider()
    raise LiveCallsDisabled('La evaluación está deshabilitada. La revisión manual sigue disponible.')
