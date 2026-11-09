"""Presentation configuration only; no conversation data is embedded in HTML."""
from django import template
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def chatbot_widget_config():
    return {
        'enabled': settings.CHATBOT_ENABLED,
        'isSimulated': settings.CHATBOT_PROVIDER == 'mock',
        'csrfCookieName': settings.CSRF_COOKIE_NAME,
        'maxInputChars': settings.CHATBOT_MAX_INPUT_CHARS,
        'requestTimeoutMs': int((settings.CHATBOT_REQUEST_TIMEOUT + 5) * 1000),
        'sessionUrl': reverse('chatbot:session'),
        'messagesUrl': reverse('chatbot:messages'),
        'resetUrl': reverse('chatbot:reset'),
        'sourcePaths': {'faq': reverse('faq:public_list'), 'catalog': reverse('catalog:list'), 'home': reverse('home')},
        'strings': {
            'assistant': _('Asistente TRUEC@'),
            'you': _('Tú'),
            'loading': _('Recuperando tu conversación…'),
            'thinking': _('El asistente está respondiendo…'),
            'remaining': _('Te quedan {remaining} de {limit} preguntas en esta sesión.'),
            'lastQuestion': _('Te queda 1 de {limit} preguntas en esta sesión.'),
            'exhausted': _('Has utilizado las {limit} preguntas de esta sesión.'),
            'failed': _('No se pudo responder a esta pregunta. No ha consumido cuota.'),
            'networkError': _('No hemos podido conectar. Puedes comprobar tu respuesta sin duplicar la pregunta.'),
            'loadError': _('No se pudo recuperar la conversación. Vuelve a intentarlo.'),
            'sessionChanged': _('Tu sesión o conversación ha cambiado. Vuelve a conectar antes de enviar tu pregunta.'),
            'unavailable': _('El asistente no está disponible en este momento. Inténtalo de nuevo más tarde.'),
            'resetRateLimited': _('Has iniciado varias conversaciones seguidas. Espera un momento y vuelve a intentarlo.'),
            'rateLimited': _('Has enviado varias preguntas seguidas. Espera un momento y vuelve a intentarlo. Tu pregunta sigue en el campo de texto.'),
            'invalidInput': _('Escribe una pregunta de hasta {max} caracteres.'),
            'retry': _('Reintentar'),
            'checkAnswer': _('Comprobar respuesta'),
            'reconnect': _('Volver a conectar'),
            'newConversation': _('Has iniciado una nueva conversación.'),
            'sources': _('Fuentes'),
            'officialInformation': _('Consultar información oficial'),
            'referenceUnavailable': _('Referencia no disponible'),
            'unconfirmedInformation': _('Información no confirmada'),
            'directions': _('Cómo llegar'),
        },
    }
