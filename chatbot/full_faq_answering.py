"""Interpret and answer with all eligible FAQ in a single provider request."""
import json
import logging
import re
from dataclasses import replace
from hashlib import sha256
from time import perf_counter

from django.utils import timezone

from locations.services import public_centers

from .full_faq_protocol import (CENTER_FIELDS, MAX_INTENTS, MAX_PARTS, FullIntent, FullOutput,
                                private_turn, redact_turn, visible_question)
from .generation import critical, validate_grounded, validate_no_faq_copy
from .grounding import AssistantReply, EvidenceSelection, GeneratedAnswer, GroundedClaim, ValidatedReference
from .knowledge.conflicts import unsafe_content
from .knowledge.full_faq import load_snapshot
from .knowledge.normalization import KnowledgeQuery, fold, words
from .knowledge.reviewed_faq import reviewed_answer, reviewed_clause
from .knowledge.sources import centers
from .language import detect_language, message_language
from .providers import ChatTurn, OpenAIProvider
from .references import MAX_SOURCES, current_sources, reference_payload
from .response_style import requested_sentences, sentence_count, sentences_in, validate_style
from .safety import ProviderError

logger = logging.getLogger(__name__)

REAL_TIME = re.compile(r'\b(?:hoy|ahora|festivos?|en este momento|today|now|currently|holidays?|at the moment)\b')
PUBLISHED_HOURS = re.compile(
    r'\b(?:horario\s+(?:publicado|habitual|normal)'
    r'|(?:published|posted|regular|usual)\s+(?:opening\s+)?(?:hours|schedule|timetable))\b'
)
LIVE_STATE = re.compile(r'(?:^|[?])\s*(?:is|are|esta|estan)\b[^?]{0,100}\b(?:open|closed|abiert[oa]s?|cerrad[oa]s?)\b')
OPENING = re.compile(r'\b(?:open\w*|clos\w*|hours|schedules?|timetables?|abiert\w*|abre\w*|cerrad\w*|horario\w*)\b')
CONTEXTUAL_CENTER = re.compile(r'\b(?:alli|ese centro|esa ubicacion|there|its|that cent(?:re|er))\b|^[¿ ]*(?:y|and)\b')
CLARIFICATION_FIELDS = re.compile(r'\b(?:horario\w*|hours|points|puntos|deadline\w*|plazo\w*)\b')
CENTER_NAME_WORDS = {'punto', 'limpio', 'centro', 'recycling', 'center', 'centre', 'de', 'del', 'la', 'el', 'the', 'of'}
CENTER_LABELS = {'address': ('Dirección', 'Address'), 'schedule': ('Horario publicado', 'Published hours'),
                 'phone': ('Teléfono', 'Phone')}


def _no_evidence(language):
    from .answering import local_text
    return AssistantReply(local_text('no_evidence', language), route='no_evidence', outcome='no_evidence')


def _reference(item, text):
    return ValidatedReference(item.source.source_id, item.source.public_url, item.content_hash, text,
                              item.source.source_type, item.title, item.source.source_version,
                              (sha256(text.encode('utf-8')).hexdigest(),))


def _center_name(text):
    return ' '.join(word for word in words(text) if word not in CENTER_NAME_WORDS)


def _center_id(name, request, question, history):
    requested = _center_name(name)
    if not requested:
        return None
    directory = [(pk, _center_name(title)) for pk, title in public_centers().values_list('pk', 'name')]
    previous = ()
    if CONTEXTUAL_CENTER.search(fold(request)):
        previous = tuple(redact_turn(turn.content) for turn in reversed(history)
                         if turn.role == 'user' and not private_turn(turn.content) and not unsafe_content(turn.content))
    for text in (request, question, *previous):
        mention = f' {_center_name(text)} '
        matches = [(pk, key) for pk, key in directory if key and f' {key} ' in mention]
        # A short name can occur inside a longer centre name.
        matches = [(pk, key) for pk, key in matches if not any(
            f' {key} ' in f' {other} ' and key != other for _, other in matches)]
        if matches:
            return matches[0][0] if len(matches) == 1 and matches[0][1] == requested else None
    return None


SCHEDULE_WORDS = dict(zip(
    'lunes martes miercoles jueves viernes sabado sabados domingo domingos de a y cerrado cerrados'.split(),
    'Monday Tuesday Wednesday Thursday Friday Saturday Saturdays Sunday Sundays from to and closed closed'.split(),
    strict=True,
))
SCHEDULE_LETTERS = re.compile(r'[^\W\d_]+')


def _schedule_line(schedule, language):
    english = language == 'en'
    if english and all(fold(token) in SCHEDULE_WORDS for token in SCHEDULE_LETTERS.findall(schedule)):
        schedule = SCHEDULE_LETTERS.sub(lambda match: SCHEDULE_WORDS[fold(match[0])], schedule)
    if detect_language(schedule) not in (None, language):
        label = 'Published hours (as listed)' if english else 'Horario publicado (texto oficial)'
    else:
        label = CENTER_LABELS['schedule'][english]
    return f'{label}: {schedule}'


def _center_part(part, request, question, history, language):
    if set(part) != {'kind', 'name', 'fields'} or not isinstance(part['name'], str):
        raise ProviderError('invalid_grounding')
    fields = part['fields']
    if (not isinstance(fields, list) or not 1 <= len(fields) <= len(CENTER_FIELDS)
            or any(field not in CENTER_FIELDS for field in fields)):
        raise ProviderError('invalid_grounding')
    scope = fold(request).lstrip('¿')
    if (LIVE_STATE.search(scope) or (REAL_TIME.search(scope) and OPENING.search(scope))
            or ('schedule' in fields and REAL_TIME.search(fold(question)) and not PUBLISHED_HOURS.search(scope))):
        return None
    center_id = _center_id(part['name'], request, question, history)
    if center_id is None:
        return None
    candidates = centers.retrieve(KnowledgeQuery((), language), timezone.now(), ids=[center_id])
    if not candidates:
        return None
    source = candidates[0]
    data = json.loads(source.content.partition('\n')[0])
    if not all(data.get(field, '').strip() for field in fields):
        return None
    english = language == 'en'
    lines = [data['name']]
    for field in dict.fromkeys(fields):
        if field == 'schedule':
            lines.append(_schedule_line(data[field], language))
        else:
            lines.append(f'{CENTER_LABELS[field][english]}: {data[field]}')
    return '\n'.join(lines), _reference(source, source.content)


def _clarification(text, language):
    from .answering import local_text

    if (not isinstance(text, str) or len(text) > 260 or not text.endswith('?') or sentence_count(text) != 1
            or critical(CLARIFICATION_FIELDS.sub('', fold(text))) or unsafe_content(text)
            or redact_turn(text) != text or '<' in text or '>' in text):
        return local_text('topic', language)
    # An English question can offer the official Spanish centre names as choices.
    prose = text
    for name in public_centers().values_list('name', flat=True):
        if name:
            prose = re.sub(rf'(?<!\w){re.escape(name)}(?!\w)', ' ', prose, flags=re.IGNORECASE)
    if detect_language(prose) not in (None, language):
        return local_text('topic', language)
    try:
        validate_style(prose, language=language)
    except ProviderError:
        return local_text('topic', language)
    return text


def _faq_part(part, knowledge, by_id, language):
    identifier = part.get('source_id')
    source = by_id.get(identifier) if isinstance(identifier, str) else None
    if source is None:
        raise ProviderError('invalid_grounding')
    kind = part.get('kind')
    if kind == 'reviewed' and set(part) == {'kind', 'source_id', 'key'} and isinstance(part['key'], str):
        reviewed = reviewed_answer(part['key'], knowledge.fragments, language)
        if not reviewed or reviewed[1].source.source_id != source.source.source_id:
            raise ProviderError('invalid_grounding')
        text = reviewed[0]
        evidence = source.content
    elif (kind == 'grounded' and set(part) == {'kind', 'source_id', 'text', 'quote'}
          and isinstance(part['text'], str) and isinstance(part['quote'], str)):
        text, evidence = part['text'], part['quote']
        reviewed = reviewed_clause(evidence, source, knowledge.fragments, language)
        if reviewed is not None:
            text = reviewed
        else:
            claim = GroundedClaim(text, (EvidenceSelection(identifier, evidence),))
            validate_grounded(GeneratedAnswer((), text, (claim,)),
                              replace(knowledge, requested_language=language))
            if any(detect_language(sentence) not in (None, language) for sentence in sentences_in(text)):
                raise ProviderError('invalid_response_style')
    else:
        raise ProviderError('invalid_grounding')
    validate_no_faq_copy(text, knowledge.fragments)
    return text, _reference(source, evidence)


def _render_intent(intent, *, knowledge, by_id, question, history, language, reasons):
    if not isinstance(intent, FullIntent) or not isinstance(intent.request, str):
        raise ProviderError('invalid_grounding')
    scope = ' '.join(words(intent.request))
    if not scope or f' {scope} ' not in f" {' '.join(words(question))} ":
        raise ProviderError('request_scope_mismatch')
    if intent.status == 'insufficient_information' and not intent.parts and not intent.question:
        yield scope, intent.status, '', None
        return
    if intent.status == 'clarification' and not intent.parts and intent.question:
        yield scope, intent.status, _clarification(intent.question, language), None
        return
    if intent.status != 'answer' or intent.question or not 1 <= len(intent.parts) <= MAX_PARTS:
        raise ProviderError('invalid_grounding')

    for part in intent.parts:
        try:
            if not isinstance(part, dict):
                raise ProviderError('invalid_grounding')
            rendered = (_center_part(part, intent.request, question, history, language) if part.get('kind') == 'center'
                        else _faq_part(part, knowledge, by_id, language))
        except ProviderError as error:
            reasons.append(error.code)
            yield scope, 'unverified', '', None
            continue
        if rendered is None:
            yield scope, 'insufficient_information', '', None
        else:
            text, reference = rendered
            yield scope, 'answer', text, reference


def _valid_reference(reference, fresh):
    item = fresh.get(reference.source_id)
    return (item is not None and item.content_hash == reference.content_hash
            and item.source.source_version == reference.source_version
            and item.source.public_url == reference.public_url)


def _render(output, *, knowledge, question, history, reasons):
    from .answering import local_text

    if not isinstance(output, FullOutput) or not 1 <= len(output.intents) <= MAX_INTENTS:
        raise ProviderError('invalid_grounding')
    if output.language not in ('es', 'en') or detect_language(question) not in (None, output.language):
        raise ProviderError('invalid_response_style')
    language = output.language
    current = visible_question(question)
    by_id = {f.source.source_id: f for f in knowledge.fragments}
    rendered, references, identifiers = [], [], set()
    for intent in output.intents:
        try:
            for scope, state, text, reference in _render_intent(
                    intent, knowledge=knowledge, by_id=by_id, question=current, history=history,
                    language=language, reasons=reasons):
                if reference is not None:
                    if reference.source_id not in identifiers and len(identifiers) >= MAX_SOURCES:
                        reasons.append('sources_limit')
                        rendered.append((scope, 'unverified', '', None))
                        continue
                    identifiers.add(reference.source_id)
                    references.append(reference)
                rendered.append((scope, state, text, reference))
        except ProviderError as error:
            reasons.append(error.code)
            rendered.append(('', 'unverified', '', None))

    fresh = current_sources(reference_payload(references)) if references else {}
    checked = []
    for scope, state, text, reference in rendered:
        if reference is not None and not _valid_reference(reference, fresh):
            reasons.append('source_changed')
            checked.append((scope, 'unverified', '', None))
        else:
            checked.append((scope, state, text, reference))

    # Only identical, validated request scopes can suppress a duplicate abstention.
    answered = {scope for scope, state, _, _ in checked if state == 'answer'}
    uncertain = {scope for scope, state, _, _ in checked if state == 'unverified'}
    texts, valid_refs, states = [], [], set()
    for scope, state, text, reference in checked:
        if state == 'insufficient_information' and scope in answered - uncertain:
            continue
        if state == 'unverified':
            text = ('I could not verify a response to part of the question.' if language == 'en'
                    else 'No he podido verificar una respuesta a una parte de la consulta.')
        texts.append(text or local_text('no_evidence', language))
        if reference is not None:
            valid_refs.append(reference)
        states.add(state)
    text = '\n\n'.join(dict.fromkeys(texts))
    sentence_limit = requested_sentences(question)
    if states == {'answer'} and sentence_limit and (sentence_count(text) != sentence_limit or text[-1] not in '.!?'):
        raise ProviderError('invalid_response_style')
    if len(states) > 1:
        route, outcome = 'generation', 'partial'
    elif 'answer' in states:
        route, outcome = 'generation', 'grounded'
    elif 'clarification' in states:
        route = outcome = 'clarification'
    else:
        route = outcome = 'no_evidence'
    return AssistantReply(text, tuple(dict.fromkeys(valid_refs)), route=route, outcome=outcome)


def answer_full(question, history, *, interface_language, provider_factory):
    from .answering import local_text
    started = perf_counter()
    user_turns = [turn.content for turn in history if turn.role == 'user']
    language = message_language(question, user_turns, interface_language)
    if unsafe_content(question):
        return AssistantReply(local_text('topic', language), route='clarification', outcome='clarification')
    if private_turn(question):
        return AssistantReply(local_text('privacy', language), route='no_evidence', outcome='no_evidence')
    knowledge = load_snapshot(language)
    if not knowledge.fragments:
        return _no_evidence(language)
    provider = provider_factory()
    status = 'provider_error'
    reasons = []
    provider_started = perf_counter()
    render_started = None
    try:
        output = provider.respond([*history, ChatTurn('user', question)], language=language, knowledge=knowledge)
        render_started = perf_counter()
        reply = _render(output, knowledge=knowledge, question=question, history=history, reasons=reasons)
        status = reply.outcome
    except ProviderError as error:
        if error.code not in ('invalid_grounding', 'invalid_response_style', 'copied_faq'):
            raise
        logger.warning('Chatbot full FAQ result rejected reason=%s version=%s', error.code, knowledge.context_version)
        reply = _no_evidence(language)
        status = reply.outcome
    finally:
        now = perf_counter()
        processing = {
            'outcome': status,
            'prepare_ms': round((provider_started - started) * 1000, 3),
            'provider_ms': round(((render_started or now) - provider_started) * 1000, 3),
            'render_ms': round((now - render_started) * 1000, 3) if render_started is not None else None,
            'validation_reasons': reasons,
        }
        if isinstance(provider, OpenAIProvider):
            provider.last_processing = processing
        metrics = getattr(provider, 'last_usage', {})
        logger.info('Chatbot full FAQ status=%s version=%s processing=%s transport=%s usage=%s',
                    status, knowledge.context_version, processing,
                    getattr(provider, 'last_transport', {}), metrics if isinstance(metrics, dict) else {})
    return reply
