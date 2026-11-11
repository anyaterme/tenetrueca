"""Public FAQ snapshots. One database read per turn, even with multiple workers."""
import json
import logging
from functools import lru_cache
from hashlib import sha256

from django.conf import settings
from django.utils import timezone

from faq.models import FAQ

from .conflicts import unsafe_content
from .contracts import KnowledgeBundle
from .reviewed_faq import FAQS, VARIANTS, definition, revision_for
from .sources.faq import fragments_for_row

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _serialize(rows):
    entries = []
    variants = {}
    for key in (*FAQS, *VARIANTS):
        reviewed = definition(key)
        description = VARIANTS[key][1] if key in VARIANTS else reviewed.title
        variants.setdefault((reviewed.title, reviewed.revision), {})[key] = description
    for pk, title, answer in rows:
        entry = {'id': f'faq:{pk}', 'title': title, 'text': answer}
        if keys := variants.get((title, revision_for(title, answer))):
            entry['reviewed'] = keys
        entries.append(entry)
    text = json.dumps(entries, ensure_ascii=False, separators=(',', ':'))
    return text, sha256(text.encode('utf-8')).hexdigest()


def load_snapshot(language):
    # Deriving the key from published rows handles edits, deletes, bulk updates,
    # rollbacks and different workers without signals or a shared cache backend.
    published = tuple(FAQ.objects.filter(is_published=True).order_by('pk').values_list('pk', 'question', 'answer'))
    safe = tuple(row for row in published if not unsafe_content(row[1] + '\n' + row[2]))
    if len(safe) != len(published):
        logger.warning('Chatbot full FAQ excluded unsafe public sources count=%s', len(published) - len(safe))
    if not safe:
        return KnowledgeBundle(language, (), (), (), 'no_evidence', '', generation_mode='full_faq')
    context, version = _serialize(safe)
    if len(context) > settings.CHATBOT_FULL_FAQ_MAX_CHARS:
        logger.warning('Chatbot full FAQ budget exceeded chars=%s sources=%s version=%s', len(context), len(safe), version)
        return KnowledgeBundle(language, (), (), (), 'no_evidence', '', ('full_faq_budget',), 'full_faq',
                               context_version=version)
    now = timezone.now()
    fragments = tuple(fragments_for_row({'id': pk, 'question': title, 'answer': answer, 'updated_at': None},
                                       now=now, method='full_faq')[0] for pk, title, answer in safe)
    return KnowledgeBundle(language, fragments, (), (), 'grounded', context, generation_mode='full_faq',
                           context_version=version)
