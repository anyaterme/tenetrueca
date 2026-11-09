"""Conservative routing and provenance checks, NOT a semantic entailment verifier.

Critical/conditional evidence stays extractive. Lexical overlap reduces obvious unrelated
claims but cannot prove a paraphrase true; real-model evaluation remains necessary.
"""
import json
import re

from django.conf import settings

from .grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim, redact_question
from .knowledge.conflicts import conflicting_claims, unsafe_content
from .knowledge.normalization import fold
from .safety import ProviderError
from .response_style import presentation_terms, sentence_count, validate_style


CRITICAL = re.compile(
    r'\d|\b(no|not|never|nunca|sin|without|except\w*|salvo|unless|only|solo|solamente|'
    r'siempre|always|si|if|cuando|when|pero|but|however|debe\w*|condicion\w*|condition\w*|prohib\w*|forbidden|'
    r'permit\w*|allow\w*|oblig\w*|must|requi\w*|necesari\w*|'
    r'puntos|points|plazo\w*|deadline\w*|caduc\w*|horario\w*|hours|'
    r'aproba\w*|aprobado\w*|approval|approved|acepta\w*|accept\w*|'
    r'garant\w*|guarant\w*|limite\w*|limit\w*|restric\w*|'
    r'negacion\w*|exclusion\w*|excluid\w*|excluded|devol\w*|refund\w*|automatic\w*)\b'
)


def critical(text):
    return bool(CRITICAL.search(fold(text)) or conflicting_claims(text))


def without_response_style(question):
    return re.sub(r'\b(?:en|in)\s+(?:[123]|una?|dos|tres|one|two|three)\s+(?:frases?|parrafos?|sentences?|paragraphs?)\b', '', fold(question))


def choose_mode(question, knowledge):
    # Output length requests are not platform numeric rules.
    question = without_response_style(question)
    if (settings.CHATBOT_GENERATION_MODE != 'grounded' or critical(question)
            or not knowledge.fragments or any(
                f.source.source_type != 'faq' or f.claim_ids or critical(f.title + '\n' + f.content)
                for f in knowledge.fragments)):
        return 'extractive'
    return 'grounded'


def grounded_format(knowledge):
    evidence = {'type': 'object', 'properties': {
        'source_id': {'type': 'string', 'enum': [f.source.source_id for f in knowledge.fragments]},
        'quote': {'type': 'string'},
    }, 'required': ['source_id', 'quote'], 'additionalProperties': False}
    claim = {'type': 'object', 'properties': {
        'text': {'type': 'string'},
        'evidence': {'type': 'array', 'items': evidence},
    }, 'required': ['text', 'evidence'], 'additionalProperties': False}
    return {'type': 'json_schema', 'name': 'trueca_grounded_answer', 'strict': True,
            'schema': {'type': 'object', 'properties': {
                'answer': {'type': 'string'}, 'sufficient': {'type': 'boolean'},
                'claims': ({'anyOf': [{'type': 'array', 'items': claim, 'minItems': 3, 'maxItems': 3},
                                      {'type': 'array', 'items': claim, 'maxItems': 0}]}
                           if knowledge.response_sentences else {'type': 'array', 'items': claim}),
            }, 'required': ['answer', 'sufficient', 'claims'], 'additionalProperties': False}}


def validate_grounded(answer, knowledge):
    """Validate shape, complete attribution, quote membership and coarse risk signals.

    Each displayed paragraph must be exactly a declared claim. No untracked preamble,
    links or model-provided metadata. Literal quote membership is only provenance.
    """
    allowed = {f.source.source_id: f for f in knowledge.fragments if f.approval == 'approved'}
    if (not isinstance(answer, GeneratedAnswer) or type(answer.sufficient) is not bool
            or not isinstance(answer.text, str) or len(answer.text) > 1800
            or not isinstance(answer.claims, tuple) or len(answer.claims) > 3):
        raise ProviderError('invalid_grounding')
    if not answer.sufficient:
        if answer.text or answer.claims or answer.selections:
            raise ProviderError('invalid_grounding')
        return answer
    if (not answer.claims or any(not isinstance(c, GroundedClaim) or not isinstance(c.text, str)
                                for c in answer.claims)
            or answer.text != '\n\n'.join(c.text for c in answer.claims)):
        raise ProviderError('invalid_grounding')
    validate_style(answer.text, language=knowledge.requested_language, sentences=knowledge.response_sentences)
    if knowledge.response_sentences and (len(answer.claims) != 3 or any(sentence_count(c.text) != 1 for c in answer.claims)):
        raise ProviderError('invalid_response_style')
    if (unsafe_content(answer.text) or critical(answer.text)
            or redact_question(answer.text) != answer.text
            or re.search(r'https?://|www\.|[<>]|[\x00-\x08\x0b\x0c\x0e-\x1f]', answer.text)):
        raise ProviderError('invalid_grounding')
    selected = []
    for claim in answer.claims:
        if (not isinstance(claim, GroundedClaim) or not claim.text.strip()
                or len(claim.text) > 900 or not 1 <= len(claim.evidence) <= 3):
            raise ProviderError('invalid_grounding')
        quoted_terms = set()
        for citation in claim.evidence:
            item = allowed.get(citation.source_id)
            if (item is None or item.source.source_type != 'faq' or critical(item.title + '\n' + item.content)
                    or not isinstance(citation.content, str) or not citation.content.strip()
                    or citation.content not in item.content
                    or len(citation.content.strip()) < min(12, len(item.content.strip()))):
                raise ProviderError('invalid_grounding')
            quoted_terms.update(presentation_terms(citation.content))
            if len(presentation_terms(claim.text).intersection(presentation_terms(citation.content))) < 2:
                raise ProviderError('invalid_grounding')
            if citation not in selected:
                selected.append(citation)
        # Coarse relevance check only. Deliberately not described as semantic validation.
        tokens = presentation_terms(claim.text)
        if not tokens or len(tokens & quoted_terms) < min(2, len(tokens)):
            raise ProviderError('invalid_grounding')
    if len({s.source_id for s in selected}) > 3:
        raise ProviderError('invalid_grounding')
    return GeneratedAnswer(tuple(selected), answer.text, answer.claims, True)


def parse_grounded(text, knowledge):
    try:
        data = json.loads(text)
        if (not isinstance(data, dict) or set(data) != {'answer', 'sufficient', 'claims'}
                or not isinstance(data['claims'], list) or len(data['claims']) > 3):
            raise ValueError
        claims = []
        for claim in data['claims']:
            if (not isinstance(claim, dict) or set(claim) != {'text', 'evidence'}
                    or not isinstance(claim['text'], str) or not isinstance(claim['evidence'], list)
                    or not 1 <= len(claim['evidence']) <= 3):
                raise ValueError
            evidence = []
            for entry in claim['evidence']:
                if (not isinstance(entry, dict) or set(entry) != {'source_id', 'quote'}
                        or not isinstance(entry['source_id'], str) or not isinstance(entry['quote'], str)):
                    raise ValueError
                evidence.append(EvidenceSelection(entry['source_id'], entry['quote']))
            claims.append(GroundedClaim(claim['text'], tuple(evidence)))
        return validate_grounded(GeneratedAnswer((), data['answer'], tuple(claims), data['sufficient']), knowledge)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ProviderError('invalid_grounding') from None
