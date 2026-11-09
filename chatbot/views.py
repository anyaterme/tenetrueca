import json
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie

from . import services


def api(method):
    def decorate(view):
        @wraps(view)
        def wrapped(request):
            if not settings.CHATBOT_ENABLED:
                return JsonResponse({'error': 'chatbot_disabled'}, status=503)
            if request.method != method:
                response = JsonResponse({'error': 'method_not_allowed'}, status=405)
                response['Allow'] = method
                return response
            try:
                return view(request)
            except services.ChatError as error:
                response = JsonResponse({'error': error.code}, status=error.status)
                if error.retry_after is not None:
                    response['Retry-After'] = str(error.retry_after)
                return response
            except DatabaseError:
                services.logger.error('Chatbot database operation failed')
                return JsonResponse({'error': 'service_unavailable'}, status=503)
        # Also keeps future ATOMIC_REQUESTS configuration from wrapping provider I/O.
        return transaction.non_atomic_requests(never_cache(wrapped))
    return decorate


def read_json(request):
    if request.content_type != 'application/json':
        raise services.ChatError('json_required', 415)
    # Bound parsing before decoding; characters may occupy up to 12 JSON escape bytes.
    max_bytes = settings.CHATBOT_MAX_INPUT_CHARS * 12 + 2048
    try:
        declared = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        raise services.ChatError('invalid_json', 400) from None
    if declared > max_bytes:
        raise services.ChatError('input_too_long', 400)
    try:
        body = request.body
        if len(body) > max_bytes:
            raise services.ChatError('input_too_long', 400)
        data = json.loads(body)
    except RequestDataTooBig:
        raise services.ChatError('input_too_long', 400) from None
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise services.ChatError('invalid_json', 400)
    if not isinstance(data, dict):
        raise services.ChatError('invalid_json', 400)
    return data


def uuid_value(value):
    if not isinstance(value, str):
        raise services.ChatError('invalid_identifier', 400)
    try:
        return UUID(value)
    except ValueError:
        raise services.ChatError('invalid_identifier', 400)


@api('GET')
@ensure_csrf_cookie
def session(request):
    identifier = request.GET.get('conversation_id')
    conversation_id = uuid_value(identifier) if identifier is not None else None
    return JsonResponse(services.get_session(services.identify_owner(request), conversation_id))


@api('POST')
def messages(request):
    data = read_json(request)
    conversation_id = uuid_value(data.get('conversation_id'))
    request_id = uuid_value(data.get('request_id'))
    content = data.get('content')
    services.validate_content(content)
    result, status = services.send_message(services.identify_owner(request), conversation_id, request_id, content)
    return JsonResponse(result, status=status)


@api('POST')
def reset(request):
    return JsonResponse(services.reset_conversation(services.identify_owner(request)))
