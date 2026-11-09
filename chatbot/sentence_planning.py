"""Lossless presentation of three independent public FAQ actions, without an LLM.

Only split a simple two-sentence instruction containing one coordinated imperative.
Never shorten evidence, translate, pad an answer, or rewrite conditional/critical rules.
The ordinary presentation and grounding validators remain authoritative.
"""
import re

from .generation import critical, validate_grounded, without_response_style
from .grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim
from .knowledge.conflicts import unsafe_content
from .knowledge.normalization import fold
from .response_style import validate_display_text, validate_style
from .safety import ProviderError


IMPERATIVES = {
    'es': {'explora', 'consulta', 'presenta', 'inicia', 'abre', 'completa', 'anade',
           'envia', 'prepara', 'revisa'},
    'en': {'explore', 'consult', 'submit', 'open', 'complete', 'add', 'prepare', 'review'},
}


def plan_three_sentences(question, knowledge):
    """Return a validated answer or decline so the existing provider path can run."""
    if (knowledge.response_sentences != 3 or len(knowledge.fragments) != 1
            or knowledge.generation_mode not in ('extractive', 'grounded')
            or critical(without_response_style(question))):
        return None
    item = knowledge.fragments[0]
    original = item.content
    if (item.source.source_type != 'faq' or item.approval != 'approved' or item.claim_ids
            or critical(item.title + '\n' + original) or unsafe_content(original)
            or re.search(r'[^\w\s.,áéíóúüñÁÉÍÓÚÜÑ@-]|[\r\n]|\.\.', original)
            or re.search(r'\b(?:o|or)\b', fold(original))):
        return None
    # Deliberately narrow grammar: two complete sentences, exactly one "and".
    # Keep literal substrings for attribution; only capitalize and punctuate display.
    sentences = re.fullmatch(r'([^\.]+)\.\s+([^\.]+)\.', original.strip())
    if not sentences:
        return None
    language = 'en' if knowledge.requested_language.lower().startswith('en') else 'es'
    conjunction = ' and ' if language == 'en' else ' y '
    quotes = [clause.strip() for sentence in sentences.groups()
              for clause in sentence.split(conjunction)]
    if (len(quotes) != 3 or any(len(q) < 12 or not q.split()
                              or fold(q.split()[0]) not in IMPERATIVES[language] for q in quotes)):
        return None
    texts = tuple(q[0].upper() + q[1:] + '.' for q in quotes)
    display = '\n\n'.join(texts)
    try:
        validate_display_text(display, original, language=knowledge.requested_language)
        validate_style(display, language=knowledge.requested_language, sentences=3)
        if knowledge.generation_mode == 'grounded':
            claims = tuple(GroundedClaim(text, (EvidenceSelection(item.source.source_id, quote),))
                           for text, quote in zip(texts, quotes))
            return validate_grounded(GeneratedAnswer((), display, claims), knowledge)
        return GeneratedAnswer((EvidenceSelection(item.source.source_id, original, display),))
    except ProviderError:
        return None
