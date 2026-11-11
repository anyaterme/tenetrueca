"""Versioned allowlist of synthetic questions and independently specified expectations."""
import json
from pathlib import Path

from .errors import EvaluationStopped
from .pricing import digest

DATA = Path(__file__).parent / 'data'
NATURAL_DATASETS = {'natural-v1', 'language-v1'}
FULL_FAQ_DATASET = 'full-faq-v1'
FULL_FAQ_DATASETS = {FULL_FAQ_DATASET, 'full-faq-v2', 'full-faq-v3', 'full-faq-v4'}
ALLOWED = {'phase2-v1', 'pilot-v1'} | FULL_FAQ_DATASETS | NATURAL_DATASETS


def load_dataset(name):
    if name not in ALLOWED:
        raise EvaluationStopped('dataset_not_allowed')
    master = json.loads((DATA / 'phase2-v1.json').read_text())
    cases = master['cases']
    fixtures = {}
    if name == 'pilot-v1':
        pilot = json.loads((DATA / 'pilot-v1.json').read_text())
        mapping = {c['id']: c for c in cases}
        cases = [mapping[key] for key in pilot['case_ids']]
    elif name in NATURAL_DATASETS | FULL_FAQ_DATASETS:
        dataset = json.loads((DATA / f'{name}.json').read_text())
        cases = dataset['cases']
        if name in FULL_FAQ_DATASETS and 'test_centers' in dataset:
            fixtures = {'centers': dataset['test_centers']}
    ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)) or any(c['language'] not in ('es', 'en') for c in cases):
        raise EvaluationStopped('dataset_invalid')
    if name == 'language-v1' and any(c.get('interface_language') not in ('es', 'en') for c in cases):
        raise EvaluationStopped('dataset_invalid')
    corpus_file = 'full-faq-corpus-v1.json' if name in FULL_FAQ_DATASETS else 'corpus-v1.json'
    return cases, digest({'name': name, 'cases': cases, 'corpus': json.loads((DATA / corpus_file).read_text()), **fixtures})
