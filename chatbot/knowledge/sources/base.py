from hashlib import sha256

from ..contracts import KnowledgeFragment, SourceRef
from ..registry import REGISTRY_VERSION, SOURCES


def fragment(kind, identifier, title, content, *, url, updated_at, now,
             relevance, method='lexical', claims=()):
    policy = SOURCES[kind]
    digest = sha256((title + '\n' + content).encode('utf-8')).hexdigest()
    return KnowledgeFragment(
        source=SourceRef(f'{kind}:{identifier}', kind, url, policy.internal_locator,
                         f'{REGISTRY_VERSION}:{digest}'),
        title=title, content=content, language=policy.language,
        authority_level=policy.authority_level, approval='approved',
        updated_at=updated_at, retrieved_at=now, relevance=relevance,
        relevance_method=method, content_hash=digest, claim_ids=claims,
    )
