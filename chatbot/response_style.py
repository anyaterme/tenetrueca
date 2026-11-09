"""Bounded presentation constraints; source text and provenance remain untouched.

Language and lexical checks catch obvious failures, not semantic equivalence of a
translation. No repair request or extra provider call is made on validation failure.
"""
import re
from dataclasses import replace

from .knowledge.normalization import fold, words, terms_for
from .safety import ProviderError


THREE_SENTENCES = re.compile(
    r'\b(?:en\s+(?:tres|3)\s+frases|in\s+(?:three|3)\s+sentences)\b', re.I)
LANGUAGE_WORDS = {
    'en': frozenset('the a an and or to of for with your you can is are sign in add submit '
                    'review object photographs proposal complete browse catalog help information '
                    'publish after clear prepare consult explore platform exchange'.split()),
    'es': frozenset('el la los las un una y o de del para con tu tus puedes es son inicia '
                    'sesion añade anade envia revision objeto fotografias propuesta completa '
                    'catalogo ayuda informacion publicar despues claras prepara consulta explora '
                    'plataforma intercambio'.split()),
}
PRESENTATION_EQUIVALENTS = {
    'photographs': 'fotografias', 'photos': 'fotografias', 'proposal': 'propuesta',
    'proposals': 'propuesta', 'propuestas': 'propuesta', 'objetos': 'objeto',
    'clear': 'claras', 'prepare': 'prepara', 'submit': 'envia', 'team': 'equipo',
    'details': 'datos', 'option': 'opcion', 'open': 'abre', 'explore': 'explora',
    'help': 'ayuda', 'information': 'informacion', 'consult': 'consulta',
    'exchange': 'intercambio', 'process': 'proceso', 'reserve': 'reserva', 'own': 'propio',
}


def presentation_terms(text):
    # Only a cross-language relevance diagnostic. Retrieval and business-rule matching
    # keep their existing dictionaries; these equivalents grant no new source access.
    return {PRESENTATION_EQUIVALENTS.get(term, term) for term in terms_for(text)}


def response_language(language):
    return 'en' if str(language).lower().replace('_', '-').split('-', 1)[0] == 'en' else 'es'


def requested_sentences(question):
    return 3 if THREE_SENTENCES.search(question) else 0


def sentences_in(text):
    # Decimal points, initials, abbreviations and ellipses are not sentence boundaries.
    masked = re.sub(r'\b(?:[A-Za-z]\.){2,}', lambda m: m[0].replace('.', '·'), text)
    masked = re.sub(r'\b(?:Sr|Sra|Dr|Dra|Mr|Mrs|Ms|etc)\.', lambda m: m[0][:-1] + '·', masked, flags=re.I)
    masked = re.sub(r'(?<=\d)\.(?=\d)|\.{2,}', '·', masked)
    parts = re.split(r'[.!?]+(?:[”’"\']*)(?=\s|$)', masked)
    return tuple(part.strip() for part in parts if part.strip())


def sentence_count(text):
    return len(sentences_in(text))


def validate_style(text, *, language, sentences=0):
    if not isinstance(text, str) or not text.strip():
        raise ProviderError('invalid_response_style')
    target = response_language(language)
    other = 'es' if target == 'en' else 'en'
    # Fail closed on clearly wrong-language prose, including a foreign body behind
    # a correctly localized preamble. Proper names and short neutral labels are fine.
    for sentence in sentences_in(text):
        tokens = words(sentence)
        scores = {key: sum(word in vocabulary for word in tokens) for key, vocabulary in LANGUAGE_WORDS.items()}
        if scores[other] >= 2 and scores[other] > scores[target]:
            raise ProviderError('invalid_response_style')
    if sentences and (sentence_count(text) != sentences or text.rstrip()[-1] not in '.!?'):
        raise ProviderError('invalid_response_style')


def needs_display_text(knowledge):
    return response_language(knowledge.requested_language) == 'en' or bool(knowledge.response_sentences)


def with_response_style(knowledge, question, language):
    return replace(knowledge, requested_language=response_language(language),
                   response_sentences=requested_sentences(question)) if knowledge is not None else None


def validate_display_text(text, original, *, language):
    from .knowledge.conflicts import unsafe_content
    from .grounding import redact_question
    if (not isinstance(text, str) or len(text) > 1800 or unsafe_content(text)
            or redact_question(text) != text or re.search(r'https?://|www\.|[<>]|[\x00-\x08\x0b\x0c\x0e-\x1f]', text)):
        raise ProviderError('invalid_response_style')
    validate_style(text, language=language)
    from .generation import critical
    # Normative/conditional units retain the stronger literal-only guarantee. A
    # generated translation must not weaken the rollback path for business rules.
    if (critical(original) and text != original) or (critical(text) and not critical(original)):
        raise ProviderError('invalid_response_style')
    source_terms = presentation_terms(original)
    display_terms = presentation_terms(text)
    if not source_terms or len(source_terms & display_terms) < min(2, len(source_terms)):
        raise ProviderError('invalid_response_style')
    # Presentation may translate or change sentence boundaries, never numeric values
    # or the presence of negation/conditions. This is a guard, not a semantic proof.
    groups = (r'\b(no|not|never|nunca|sin|without|cannot)\b',
              r'\b(solo|solamente|only)\b', r'\b(except\w*|salvo|unless)\b',
              r'\b(si|if|cuando|when)\b', r'\b(debe\w*|must|requi\w*)\b')
    if (re.findall(r'\d+(?:[.,]\d+)*', original) != re.findall(r'\d+(?:[.,]\d+)*', text)
            or any(bool(re.search(group, fold(original))) != bool(re.search(group, fold(text))) for group in groups)):
        raise ProviderError('invalid_response_style')
