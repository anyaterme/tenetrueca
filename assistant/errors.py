"""Bounded diagnostics: never store response text, values or exception messages."""
from pydantic import ValidationError
from openai import APIError, APIStatusError

from assistant.schema import EvaluationResult


class ResponseFailure(ValueError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.code = code
        self.details = details


def check_response(response):
    if response.status == 'incomplete':
        reason = response.incomplete_reason
        raise ResponseFailure(
            'response_incomplete', stage='response',
            reason=reason if reason in ('max_output_tokens', 'content_filter') else 'unknown',
        )
    if response.status != 'completed':
        raise ResponseFailure('response_failed', stage='response')
    if response.refused:
        raise ResponseFailure('response_refused', stage='response')
    if response.payload is None or (
        isinstance(response.payload, str) and not response.payload.strip()
    ):
        raise ResponseFailure('response_empty', stage='response')


def safe_error_details(error):
    if isinstance(error, ResponseFailure):
        return error.details
    if isinstance(error, APIStatusError):
        allowed_codes = {'invalid_json_schema', 'invalid_api_key', 'model_not_found',
                         'unsupported_value', 'unsupported_parameter', 'invalid_value',
                         'context_length_exceeded', 'insufficient_quota'}
        allowed_parameters = {'model', 'text.format.schema', 'text.format', 'reasoning.effort',
                              'max_output_tokens', 'input', 'service_tier'}
        return {'stage': 'http', 'status': error.status_code,
                'provider_code': error.code if isinstance(error.code, str)
                and error.code in allowed_codes else 'other',
                'parameter': error.param if isinstance(error.param, str)
                and error.param in allowed_parameters else 'other'}
    if isinstance(error, APIError):
        return {'stage': 'provider', 'exception_type': 'APIError'}
    if not isinstance(error, ValidationError):
        # Only fixed class names, never arbitrary exception messages/arguments.
        for kind in (KeyError, AttributeError, ImportError, OSError, RuntimeError):
            if isinstance(error, kind):
                return {'stage': 'internal', 'exception_type': kind.__name__}
        return {}
    schema = EvaluationResult.model_json_schema()
    known_fields = set(schema['properties'])
    for definition in schema.get('$defs', {}).values():
        known_fields.update(definition.get('properties', {}))
    violations = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:10]:
        # Extra field names can themselves contain private/model-generated text.
        path = '.'.join(str(part) if isinstance(part, int) or part in known_fields else '?'
                        for part in item['loc'][:8])
        violations.append({'field': path or '$', 'type': item['type']})
    return {'stage': 'schema', 'violations': violations}


ERROR_MESSAGES = {
    'image_preparation_failed': 'No se han podido preparar las fotografías para la evaluación. '
                                'No se han enviado originales. La revisión manual sigue disponible.',
    'invalid_response': 'La respuesta no cumple el formato o las reglas de validación. '
                        'En fallos antiguos puede no haber un diagnóstico guardado.',
    'response_incomplete': 'La respuesta quedó incompleta y no se ha reintentado automáticamente.',
    'response_refused': 'El modelo rechazó generar la evaluación. No se ha reintentado.',
    'response_empty': 'El proveedor no devolvió contenido para evaluar.',
    'response_failed': 'El proveedor no completó la respuesta.',
    'invalid_request': 'El proveedor rechazó los parámetros o el esquema de la solicitud.',
    'authentication_error': 'El proveedor rechazó las credenciales del servidor.',
    'permission_denied': 'La cuenta no tiene permiso para realizar esta solicitud.',
    'model_not_found': 'El modelo solicitado no está disponible para esta cuenta.',
    'server_error_unknown_usage': 'El proveedor devolvió un error de servidor. '
                                  'El consumo puede ser desconocido; no se ha reintentado.',
}
