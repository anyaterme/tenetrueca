"""Verifiable generation contract: complete evidence units, never invented citations."""
import json
import re
from dataclasses import dataclass

from .safety import ProviderError


@dataclass(frozen=True)
class EvidenceSelection:
    source_id: str
    content: str
    display_text: str = ''


@dataclass(frozen=True)
class GeneratedAnswer:
    selections: tuple[EvidenceSelection, ...]
    text: str = ''
    claims: tuple['GroundedClaim', ...] = ()
    sufficient: bool = True


@dataclass(frozen=True)
class GroundedClaim:
    text: str
    evidence: tuple[EvidenceSelection, ...]


@dataclass(frozen=True)
class ValidatedReference:
    source_id: str
    public_url: str | None
    content_hash: str
    supported_text: str
    source_type: str = ''
    title: str = ''
    source_version: str | None = None
    evidence_hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssistantReply:
    text: str
    references: tuple[ValidatedReference, ...] = ()
    route: str = 'local'
    outcome: str = 'grounded'


def response_format(knowledge):
    if knowledge.generation_mode == 'grounded':
        from .generation import grounded_format
        return grounded_format(knowledge)
    from .response_style import needs_display_text
    display = needs_display_text(knowledge)
    return {'type': 'json_schema', 'name': 'trueca_grounded_selection', 'strict': True,
            'schema': {'type': 'object', 'properties': {'selections': {
                'type': 'array', 'items': {'type': 'object', 'properties': {
                    'source_id': {'type': 'string', 'enum': [f.source.source_id for f in knowledge.fragments]},
                    'content': {'type': 'string'},
                    **({'display_text': {'type': 'string'}} if display else {}),
                }, 'required': ['source_id', 'content'] + (['display_text'] if display else []), 'additionalProperties': False},
            }}, 'required': ['selections'], 'additionalProperties': False}}


def parse_answer(text, knowledge):
    if knowledge.generation_mode == 'grounded':
        from .generation import parse_grounded
        return parse_grounded(text, knowledge)
    try:
        from .response_style import needs_display_text, validate_display_text, validate_style
        display = needs_display_text(knowledge)
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {'selections'} or not isinstance(data['selections'], list):
            raise ValueError
        if len(data['selections']) > 2:
            raise ValueError
        allowed = {f.source.source_id: f.content for f in knowledge.fragments if f.approval == 'approved'}
        selected = []
        for item in data['selections']:
            if (not isinstance(item, dict) or set(item) != ({'source_id', 'content', 'display_text'} if display else {'source_id', 'content'})
                    or not isinstance(item['source_id'], str) or not isinstance(item['content'], str)
                    or item['source_id'] not in allowed or item['content'] != allowed[item['source_id']]):
                raise ValueError
            shown = item.get('display_text', '')
            if display:
                validate_display_text(shown, item['content'], language=knowledge.requested_language)
            selection = EvidenceSelection(item['source_id'], item['content'], shown)
            if any(s.source_id == selection.source_id and s != selection for s in selected):
                raise ProviderError('invalid_response_style')
            if selection not in selected:
                selected.append(selection)
        if selected and display:
            validate_style('\n\n'.join(s.display_text for s in selected), language=knowledge.requested_language,
                           sentences=knowledge.response_sentences)
        return GeneratedAnswer(tuple(selected))
    except (ValueError, TypeError, RecursionError):
        raise ProviderError('invalid_grounding') from None


def redact_question(text):
    # Historical private prose is not sent at all. Redact common personal fields from
    # the current original question without changing the message persisted by Django.
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[dato omitido]', text)
    text = re.sub(r'\b(?:[XYZ]\d{7}|\d{8})[A-Z]\b', '[dato omitido]', text, flags=re.I)
    return re.sub(r'(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)', '[dato omitido]', text)
