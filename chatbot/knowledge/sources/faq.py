"""Published FAQ only. PostgreSQL FTS and deterministic lexical SQLite fallback."""
from django.db import connection
from django.urls import reverse
import re

from faq.models import FAQ

from ..conflicts import conflicting_claims, unsafe_content
from ..normalization import lexical_score
from ..registry import CANDIDATE_LIMIT, SOURCES
from .base import fragment


FTS_EQUIVALENTS = {
    'registro': ('registro', 'registrarse', 'registration', 'register'),
    'activacion': ('activación', 'activation'),
    'puntos': ('puntos', 'points'),
    'entrega': ('entrega', 'entregar', 'delivery', 'deposit'),
    'publicar': ('publicar', 'publicación', 'publish', 'publishing'),
    'moderacion': ('moderación', 'moderation'),
    'humana': ('humana', 'humano', 'human'),
    'categoria': ('categoría', 'category', 'categories'),
    'horario': ('horario', 'schedule', 'opening', 'hours'),
    'centro': ('centro', 'center', 'centre'),
}


def retrieve(query, now):
    if not query.terms:
        return []
    rows = FAQ.objects.filter(is_published=True).values(*SOURCES['faq'].fields)
    if connection.vendor == 'postgresql':
        rows = _postgres_candidates(rows, query)
        method = 'postgres_fts'
    else:
        # Stream all published rows so a highly relevant late-position FAQ isn't missed.
        # Keep at most CANDIDATE_LIMIT winners in memory, not a persistent corpus copy.
        rows = rows.order_by('position', 'id').iterator(chunk_size=100)
        method = 'lexical'
    winners = []
    for row in rows:
        score = lexical_score(query, row['question'], row['answer'])
        if method == 'postgres_fts':
            rank = float(row['search_rank'])
            # Native stem matches may have no exact normalized token match.
            score += rank / (1 + rank)
        if score <= 0:
            continue
        winners.append((score, row))
        winners.sort(key=lambda item: (-item[0], item[1]['position'], item[1]['id']))
        del winners[CANDIDATE_LIMIT:]
    found = []
    for score, row in winners:
        found.extend(fragments_for_row(row, now=now, score=score, method=method))
    return found


def fragments_for_row(row, *, now, score=1, method='public_revalidation'):
    """Same complete units for retrieval and batched historical provenance checks."""
    found = []
    units = [row['answer']]
    paragraphs = re.split(r'\n\s*\n', row['answer'])
    # Only explicit independent paragraphs can be separated. A connected sentence,
    # exception or pronoun-dependent paragraph is never cut to rescue a partial rule.
    if (len(paragraphs) > 1 and conflicting_claims(row['answer'])
            and not unsafe_content(row['question'] + '\n' + row['answer'])
            and not re.search(r'\b(excepto|salvo|unless|except|however|solo si|only if|a menos)\b', row['answer'], re.I)
            and not any(re.match(r'\s*(excepto|salvo|pero|sin embargo|unless|except|however|eso|estos|those|it)\b', p, re.I)
                        for p in paragraphs)):
        units = paragraphs
    for index, content in enumerate(units):
        identifier = row['id'] if len(units) == 1 else f"{row['id']}:unit:{index}"
        found.append(fragment(
            'faq', identifier, row['question'], content,
            url=f"{reverse('faq:public_list')}#faq-question-{row['id']}",
            updated_at=row['updated_at'], now=now, relevance=score, method=method,
        ))
    return found


def _postgres_candidates(rows, query):
    from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

    # The schema has no source language column. Search both supported dictionaries;
    # this does not label the original document as a verified ES/EN translation.
    vector = None
    search = None
    for config in ('spanish', 'english'):
        part = (SearchVector('question', config=config, weight='A')
                + SearchVector('answer', config=config, weight='B'))
        vector = part if vector is None else vector + part
        for term in tuple(dict.fromkeys(query.terms + query.literal_terms)):
            # Terms are literal text, not raw tsquery, SQL or user-selected config.
            for equivalent in FTS_EQUIVALENTS.get(term, (term,)):
                part_query = SearchQuery(equivalent, config=config, search_type='plain')
                search = part_query if search is None else search | part_query
    return rows.annotate(search_rank=SearchRank(vector, search)).filter(
        search_rank__gt=0,
    ).order_by('-search_rank', 'position', 'id')[:CANDIDATE_LIMIT]
