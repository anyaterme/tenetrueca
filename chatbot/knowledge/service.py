"""Read-only evidence retrieval; conversation resolution and generation are separate."""
from django.conf import settings
from django.utils import timezone
from django.utils.translation import get_language

from .conflicts import conflicting_claims
from .context import KnowledgeLimits, build_context
from .contracts import KnowledgeBundle
from .normalization import is_platform_overview, normalize_query
from .sources import categories, centers, faq, rules


class KnowledgeService:
    def retrieve(self, question: str, *, language: str | None = None) -> KnowledgeBundle:
        limits = KnowledgeLimits.configured()
        query = normalize_query(
            question, language=language or get_language() or settings.LANGUAGE_CODE,
            max_chars=settings.CHATBOT_MAX_INPUT_CHARS,
        )
        query_conflicts = conflicting_claims(question)
        # A single unresolved claim has no approved normative answer. The conversation
        # planner handles separable mixed questions individually.
        if query_conflicts:
            return KnowledgeBundle(query.language, (), (), query_conflicts, 'conflict', '')
        overview = is_platform_overview(question)
        if not query.terms and not overview:
            return KnowledgeBundle(query.language, (), (), (), 'no_evidence', '')
        now = timezone.now()
        rule_fragments, facts, drift = rules.retrieve(query, now, include_overview=overview)
        # Database exceptions intentionally propagate. Never fall back to stale data or a
        # partial "official" bundle when any source is unavailable.
        candidates = (rule_fragments + faq.retrieve(query, now)
                      + categories.retrieve(query, now) + centers.retrieve(query, now))
        fragments, context, conflicts, exclusions = build_context(candidates, limits=limits, now=now)
        conflicts = tuple(sorted(set(conflicts).union(drift)))
        included = {item.source.source_id for item in fragments}
        facts = tuple(fact for fact in facts if set(fact.source_ids).issubset(included))
        outcome = 'conflict' if conflicts else 'grounded' if fragments else 'no_evidence'
        return KnowledgeBundle(query.language, fragments, facts, conflicts, outcome, context, exclusions)
