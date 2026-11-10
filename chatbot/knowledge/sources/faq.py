"""Published FAQ only. PostgreSQL FTS and deterministic lexical SQLite fallback."""
from django.db import connection
from django.urls import reverse

from faq.models import FAQ

from ..normalization import lexical_score, terms_for
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
    wanted = set(query.terms)
    for row in rows:
        # "Points" alone cannot make a charge FAQ authoritative for a registration
        # award. Publication grants authority, not relevance to a different event.
        available = set(terms_for(row['question'] + '\n' + row['answer']))
        if 'puntos' in wanted:
            groups = ({'registro', 'activacion'}, {'entrega', 'aceptada'}, {'publicar'})
            point_events = [group for group in groups if wanted.intersection(group)]
            if point_events and ('puntos' not in available or any(
                    not available.intersection(group) for group in point_events)):
                continue
        # Mentioning public centers does not answer their opening hours or contact
        # details. Keep structured public evidence as fallback for missing FAQ facts.
        if wanted.intersection({'centro', 'limpio'}):
            fields = wanted.intersection({'horario', 'direccion', 'telefono'})
            if not fields.issubset(available):
                continue
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
    # Publication authorizes the entire answer. Never discard a paragraph, amount,
    # deadline, condition or exception to rescue another part of an official FAQ.
    return [fragment(
        'faq', row['id'], row['question'], row['answer'],
        url=f"{reverse('faq:public_list')}#faq-question-{row['id']}",
        updated_at=row['updated_at'], now=now, relevance=score, method=method,
    )]


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
