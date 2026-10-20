import hashlib
import json
import re

from catalog.models import Category, public_category_ids
from assistant.rules import PROMPT_VERSION, RULES_VERSION


def redact_personal_data(text):
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[correo omitido]', text)
    return re.sub(r'(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)', '[teléfono omitido]', text)


def selected_classification(publication, categories):
    """The publication FK stores the selected leaf, not a separate root category."""
    path = []
    visited = set()
    nodes = {category['id']: category for category in categories}
    node_id = publication.category_id
    while node_id is not None and node_id not in visited:
        visited.add(node_id)
        node = nodes.get(node_id) or Category.objects.filter(pk=node_id).values(
            'id', 'name', 'parent_id',
        ).first()
        if node is None:
            break
        path.insert(0, node)
        node_id = node['parent_id']
    complete = bool(path) and node_id is None
    root = path[0] if complete else None
    allowed_ids = {category['id'] for category in categories}
    return {
        'category': root,
        'subcategory': path[-1] if complete and len(path) > 1 else None,
        'path': path,
        'requires_subcategory': bool(root) and any(
            category['parent_id'] == root['id'] for category in categories
        ),
        'is_available': complete and all(item['id'] in allowed_ids for item in path),
    }


def publication_snapshot(publication):
    """No account, center, location or contact fields are sent to the provider."""
    categories = list(Category.objects.filter(pk__in=public_category_ids())
                      .order_by('pk').values('id', 'name', 'parent_id'))
    raw = {
        'rules_version': RULES_VERSION, 'prompt_version': PROMPT_VERSION,
        'title': publication.title, 'description': publication.description,
        'category_id': publication.category_id,
        'classification': selected_classification(publication, categories),
        'submitted_at': publication.submitted_at.isoformat() if publication.submitted_at else None,
        'photos': [
            {'id': photo.pk, 'name': photo.image.name, 'updated_at': photo.updated_at.isoformat()}
            for photo in publication.photos.order_by('-is_primary', 'sort_order', 'pk')
        ],
        'categories': categories,
    }
    fingerprint = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    raw['title'] = redact_personal_data(raw['title'])
    raw['description'] = redact_personal_data(raw['description'])
    return fingerprint, raw
