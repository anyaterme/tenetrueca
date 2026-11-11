"""Choose between the two supported chat languages before resolving context."""
import re

from .knowledge.normalization import words


# Words shared by both languages and product names are neutral. Include the
# short follow-ups and vocabulary people use in the publication and pickup flows.
MARKERS = {
    'es': frozenset(
        'al antes articulo articulos ayuda bien buenas buenos como consigo con cuanto cuantos cuenta '
        'de debo del dejar despues dias donde el ella en entrega entregar es esta estas este fotos '
        'gracias hago hay hola horario la las llevar lo los luego mas mi mis necesito nunca objeto objetos '
        'para paso pero por producto productos propuesta puedo puede publicar puntos que quiero recoger '
        'registro ropa saldo se si sin sobre tengo tiene una unas uno unos y ya yo'.split()
    ),
    'en': frozenset(
        'about after am an and are before book booking bring but by can clothes collect could days do does '
        'drop enough for from good have help hello hi how i if in is it item items may my need next '
        'of off on or our out photos please points post proposal publish pickup receive register review '
        'should sign submit than thank thanks that the their them then there they this to was we what '
        'when where which who will with without would yes you your'.split()
    ),
}
NEUTRAL_SPANS = re.compile(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|«[^»]*»|“[^”]*”|"[^"]*"')


def detect_language(text):
    """Return a clear Spanish or English signal, or None for ambiguous text."""
    text = NEUTRAL_SPANS.sub(' ', text)
    tokens = set(words(text))
    spanish = len(tokens & MARKERS['es']) + int('¿' in text or '¡' in text)
    english = len(tokens & MARKERS['en'])
    if spanish == english:
        return None
    return 'es' if spanish > english else 'en'


def message_language(question, recent_users, interface_language):
    language = detect_language(question)
    if language:
        return language
    for turn in reversed(tuple(recent_users)):
        if language := detect_language(turn):
            return language
    return 'en' if str(interface_language).replace('_', '-').lower().split('-', 1)[0] == 'en' else 'es'
