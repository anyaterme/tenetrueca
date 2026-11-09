"""Public reference snapshots and batched, fail-closed historical presentation.

No evidence text or internal locator is stored. At most three sources per reply and
100 distinct IDs per history read are revalidated; overflow appears unavailable.
"""
import re
import json
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse
from django.utils import timezone

from faq.models import FAQ

from .knowledge.conflicts import conflicting_claims, unsafe_content
from .knowledge.normalization import KnowledgeQuery
from .knowledge.registry import APPROVED_CLAIMS, SOURCES
from .knowledge.sources import categories, centers, faq, rules
from .knowledge.point_policy import unverified_point_policy

MAX_SOURCES = 3
MAX_HISTORY_SOURCES = 100


def directions_url(item):
    """Reuse only the public URL already built by locations.center_map_data."""
    if item.source.source_type != 'center':
        return None
    try:
        value = json.loads(item.content.partition('\n')[0]).get('directionsUrl')
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True)
        lat, lng = map(float, query['destination'][0].split(','))
        if (parsed.scheme == 'https' and parsed.netloc == 'www.google.com' and parsed.path == '/maps/dir/'
                and not parsed.fragment and set(query) == {'api', 'destination'} and query['api'] == ['1']
                and len(query['destination']) == 1 and -90 <= lat <= 90 and -180 <= lng <= 180):
            return value
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def valid_public_url(item):
    url = item.source.public_url
    if url is None:
        return item.source.source_type == 'business_rule'
    if not isinstance(url, str) or not url.startswith('/') or url.startswith('//') or any(c in url for c in ('\\', '\r', '\n', '\t')):
        return False
    parsed = urlsplit(url)
    kind, _, identifier = item.source.source_id.partition(':')
    if parsed.scheme or parsed.netloc:
        return False
    if kind == 'faq':
        pk = identifier.partition(':')[0]
        return pk.isdecimal() and url == f"{reverse('faq:public_list')}#faq-question-{pk}"
    if kind == 'center':
        return url == reverse('home') + '#puntos-limpios'
    if kind == 'category':
        query = parse_qs(parsed.query, keep_blank_values=True)
        return (parsed.path == reverse('catalog:list') and not parsed.fragment
                and len(query) == 1 and next(iter(query)) in ('category', 'subcategory')
                and next(iter(query.values())) == [identifier])
    return False


def reference_payload(references):
    result = {}
    for reference in references:
        if reference.source_id not in result and len(result) >= MAX_SOURCES:
            continue
        if not reference.source_type or not reference.title:
            continue
        entry = result.setdefault(reference.source_id, {
            'source_id': reference.source_id, 'type': reference.source_type,
            'title': reference.title, 'url': reference.public_url,
            'content_hash': reference.content_hash, 'version': reference.source_version,
            'evidence_hashes': [],
        })
        for digest in reference.evidence_hashes:
            if digest not in entry['evidence_hashes']:
                entry['evidence_hashes'].append(digest)
    return list(result.values())


def current_sources(stored):
    """Constant number of source queries regardless of the number of history links."""
    identifiers = list(dict.fromkeys(r.get('source_id') for r in stored
                                    if isinstance(r, dict) and isinstance(r.get('source_id'), str)))[:MAX_HISTORY_SOURCES]
    faq_ids, center_ids, slugs = set(), set(), set()
    for identifier in identifiers:
        match = re.fullmatch(r'faq:(\d+)(?::unit:\d+)?', identifier)
        if match:
            faq_ids.add(int(match[1]))
        match = re.fullmatch(r'center:(\d+)', identifier)
        if match:
            center_ids.add(int(match[1]))
        if identifier.startswith('category:'):
            slugs.add(identifier.partition(':')[2])
    now = timezone.now()
    query = KnowledgeQuery(('puntos', 'registro', 'entrega', 'publicar', 'moderacion', 'humana'), 'es')
    found = []
    if faq_ids:
        for row in FAQ.objects.filter(pk__in=faq_ids, is_published=True).values(*SOURCES['faq'].fields):
            found.extend(faq.fragments_for_row(row, now=now))
    if center_ids:
        found.extend(centers.retrieve(query, now, ids=center_ids))
    if slugs:
        found.extend(categories.retrieve(query, now, slugs=slugs))
    if any(i.startswith('business_rule:') for i in identifiers):
        found.extend(rules.retrieve(query, now, include_overview='business_rule:platform.overview' in identifiers)[0])
    return {f.source.source_id: f for f in found if f.source.source_id in identifiers
            and f.approval == 'approved' and not unsafe_content(f.title + '\n' + f.content)
            and not conflicting_claims(f.title + '\n' + f.content)
            and not unverified_point_policy(f)
            and (not f.claim_ids or set(f.claim_ids).issubset(APPROVED_CLAIMS))
            and valid_public_url(f)}


def present_sources(messages):
    stored = [r for m in reversed(messages) if m.role == 'assistant' and isinstance(m.sources, list)
              for r in m.sources[:MAX_SOURCES]]
    current = current_sources(stored)
    result = {}
    for message in messages:
        public = []
        for entry in (message.sources[:MAX_SOURCES] if message.role == 'assistant' and isinstance(message.sources, list) else []):
            identifier = entry.get('source_id') if isinstance(entry, dict) else None
            item = current.get(identifier) if isinstance(identifier, str) else None
            if (item is None or item.content_hash != entry.get('content_hash')
                    or item.source.source_version != entry.get('version')
                    or item.source.public_url != entry.get('url')
                    or item.source.source_type != entry.get('type')):
                # No stale titles/types/URLs or administrative reason leak after withdrawal.
                public.append({'available': False})
            else:
                data = {'title': item.title, 'url': item.source.public_url,
                        'type': item.source.source_type, 'available': True}
                directions = directions_url(item)
                if directions:
                    data['directions_url'] = directions
                public.append(data)
        result[message.pk] = public
    return result
