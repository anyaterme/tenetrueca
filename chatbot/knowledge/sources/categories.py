from urllib.parse import urlencode

from django.urls import reverse

from catalog.models import Category, public_category_ids

from ..normalization import lexical_score
from ..registry import CANDIDATE_LIMIT, SOURCES
from .base import fragment


def retrieve(query, now, *, slugs=None):
    rows = {row['id']: row for row in Category.objects.filter(
        pk__in=public_category_ids(),
    ).values(*SOURCES['category'].fields)}
    found = []
    for row in rows.values():
        if slugs is not None and row['slug'] not in slugs:
            continue
        path = []
        current = row
        visited = set()
        while current is not None and current['id'] not in visited:
            visited.add(current['id'])
            path.insert(0, current['name'])
            current = rows.get(current['parent_id'])
        title = ' / '.join(path)
        content = f'Categoría del catálogo público: {title}. Su existencia no garantiza la admisión de un objeto.'
        score = lexical_score(query, title, content + ' categoria catalogo')
        if slugs is not None:
            score = 1
        if score > 0:
            parameter = 'category' if row['parent_id'] is None else 'subcategory'
            found.append(fragment(
                'category', row['slug'], title, content,
                url=reverse('catalog:list') + '?' + urlencode({parameter: row['slug']}),
                updated_at=row['updated_at'], now=now, relevance=score,
            ))
    ordered = sorted(found, key=lambda item: (-item.relevance, item.source.source_id))
    return ordered[:CANDIDATE_LIMIT] if slugs is None else ordered
