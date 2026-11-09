"""Immutable retrieval contracts; relevance is a search score, never a truth probability."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    source_type: Literal['faq', 'category', 'center', 'business_rule']
    public_url: str | None
    internal_locator: str  # Backend provenance only: excluded from serialized context.
    source_version: str | None


@dataclass(frozen=True)
class KnowledgeFragment:
    source: SourceRef
    title: str
    content: str
    language: str
    authority_level: int
    approval: Literal['approved', 'unreviewed', 'blocked']
    updated_at: datetime | None
    retrieved_at: datetime
    relevance: float
    relevance_method: str
    content_hash: str
    claim_ids: tuple[str, ...] = ()
    effective_from: datetime | None = None
    effective_to: datetime | None = None


@dataclass(frozen=True)
class RuleFact:
    claim_id: str
    value: int | str | bool
    unit: str | None
    event: str
    scope: str
    conditions: tuple[str, ...]
    source_ids: tuple[str, ...]
    validation: Literal['verified', 'requires_review', 'conflict'] = 'verified'
    localized_content: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class KnowledgeBundle:
    requested_language: str
    fragments: tuple[KnowledgeFragment, ...]
    rules: tuple[RuleFact, ...]
    conflict_ids: tuple[str, ...]
    outcome: Literal['grounded', 'no_evidence', 'conflict', 'clarification']
    context: str
    # Safe reason codes only. Never document content, private IDs or exception details.
    exclusions: tuple[str, ...] = ()
    generation_mode: Literal['extractive', 'grounded'] = 'extractive'
    response_sentences: int = 0
