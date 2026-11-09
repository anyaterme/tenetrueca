import json

from django.urls import reverse

from locations.services import center_map_data, public_centers

from ..normalization import lexical_score
from ..registry import CANDIDATE_LIMIT, SOURCES
from .base import fragment


def retrieve(query, now, *, ids=None):
    found = []
    # only() prevents loading the email/services columns even though this model has them.
    queryset = public_centers()
    if ids is not None:
        queryset = queryset.filter(pk__in=ids)
    for center in queryset.only(*SOURCES['center'].fields).iterator(chunk_size=100):
        public = center_map_data([center])[0]
        # id is already public in map data, but don't duplicate model identifiers in text.
        public.pop('id')
        content = json.dumps(public, ensure_ascii=False, sort_keys=True)
        content += '\nEl estado operativo registrado no indica si está abierto en tiempo real.'
        score = lexical_score(query, center.name, content + ' centro punto limpio horario direccion telefono')
        if ids is not None:
            score = 1
        if score > 0:
            found.append(fragment(
                'center', center.pk, center.name, content,
                url=reverse('home') + '#puntos-limpios', updated_at=center.updated_at,
                now=now, relevance=score,
            ))
            found.sort(key=lambda item: (-item.relevance, item.source.source_id))
            if ids is None:
                del found[CANDIDATE_LIMIT:]
    return found
