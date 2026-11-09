"""Explicit source and field allowlists. No model discovery or caller-selected sources."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class SourcePolicy:
    fields: tuple[str, ...]
    authority_level: int
    # These models have no language column: don't infer a translation from Django's locale.
    language: str = 'und'
    internal_locator: str = ''


SOURCES = MappingProxyType({
    'faq': SourcePolicy(('id', 'question', 'answer', 'position', 'created_at', 'updated_at'), 3,
                        internal_locator='faq.models.FAQ'),
    'category': SourcePolicy(('id', 'name', 'slug', 'parent_id', 'sort_order', 'updated_at'), 2,
                             internal_locator='catalog.models.Category/public_category_ids'),
    'center': SourcePolicy(('id', 'name', 'address', 'latitude', 'longitude', 'phone',
                            'schedule', 'operational_status', 'public_information', 'updated_at'), 2,
                            internal_locator='locations.services.public_centers/center_map_data'),
    'business_rule': SourcePolicy((), 1, 'es', 'chatbot.knowledge.sources.rules.RULES'),
})

# Candidate bound, independent of final context size. SQLite scans in chunks with bounded
# memory; PostgreSQL ranks in SQL before selecting candidates. No document cache.
CANDIDATE_LIMIT = 50
REGISTRY_VERSION = 'knowledge-1'
APPROVED_CLAIMS = frozenset({
    'platform.overview',
    'points.registration', 'points.accepted_delivery', 'points.publication', 'moderation.human_decision',
})
BLOCKED_CLAIMS = MappingProxyType({
    'reservation.pickup_window': 'D1',
    'points.reservation_charge': 'D3',
    'admission.small_appliance': 'D6',
})
