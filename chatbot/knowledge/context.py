"""Complete semantic units only: never truncate an answer, exception or condition."""
import json
from dataclasses import dataclass

from django.conf import settings

from .conflicts import conflicting_claims, unsafe_content
from .registry import BLOCKED_CLAIMS, official_faq


@dataclass(frozen=True)
class KnowledgeLimits:
    max_fragments: int
    fragment_chars: int
    context_chars: int

    @classmethod
    def configured(cls):
        values = [getattr(settings, name, default) for name, default in (
            ('CHATBOT_KNOWLEDGE_MAX_FRAGMENTS', 5),
            ('CHATBOT_KNOWLEDGE_FRAGMENT_CHARS', 1200),
            ('CHATBOT_KNOWLEDGE_CONTEXT_CHARS', 6000),
        )]
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError('invalid_knowledge_limits')
        return cls(*values)


def _public_payload(fragment):
    # Explicit projection: never dataclasses.asdict(SourceRef), which includes provenance.
    return {
        'source_id': fragment.source.source_id, 'source_type': fragment.source.source_type,
        'title': fragment.title, 'content': fragment.content, 'language': fragment.language,
        'public_url': fragment.source.public_url, 'authority_level': fragment.authority_level,
        'updated_at': fragment.updated_at.isoformat() if fragment.updated_at else None,
    }


def _encode(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def build_context(candidates, *, limits, now):
    """Return selected fragments, JSON evidence, conflict IDs and safe exclusion codes."""
    selected, entries, conflicts, excluded = [], [], set(), set()
    seen = set()
    seen_content = set()
    for item in sorted(candidates, key=lambda f: (
            not official_faq(f), -f.relevance, f.authority_level,
            -(f.updated_at.timestamp() if f.updated_at else 0), f.source.source_id)):
        text = item.title + '\n' + item.content
        blocked = (set() if official_faq(item) else
                   set(conflicting_claims(text)) | set(item.claim_ids).intersection(BLOCKED_CLAIMS))
        if blocked:
            conflicts.update(blocked)
            excluded.add('conflicting_content')
            continue
        if unsafe_content(text):
            excluded.add('unsafe_content')
            continue
        if (item.approval != 'approved' or item.relevance <= 0
                or item.effective_from is not None and item.effective_from > now
                or item.effective_to is not None and item.effective_to <= now):
            excluded.add('not_eligible')
            continue
        key = item.source.source_id
        if key in seen or item.content_hash in seen_content:
            continue
        seen.add(key)
        seen_content.add(item.content_hash)
        entry = _public_payload(item)
        # Bound the *serialized* fragment, including title, metadata and URLs. Whole FAQ
        # units that do not fit are omitted, not shortened. Context framing also counts.
        if len(_encode(entry)) > limits.fragment_chars:
            excluded.add('fragment_too_large')
            continue
        if (len(selected) >= limits.max_fragments
                or len(_encode({'sources': entries + [entry]})) > limits.context_chars):
            excluded.add('context_limit')
            continue
        selected.append(item)
        entries.append(entry)
    return (tuple(selected), _encode({'sources': entries}) if entries else '',
            tuple(sorted(conflicts)), tuple(sorted(excluded)))
