"""Versioned allowlist of synthetic questions and independently specified expectations."""
import json
from pathlib import Path

from .errors import EvaluationStopped
from .pricing import digest

DATA = Path(__file__).parent / 'data'
ALLOWED = {'phase2-v1', 'pilot-v1', 'natural-v1'}


def load_dataset(name):
    if name not in ALLOWED:
        raise EvaluationStopped('dataset_not_allowed')
    master = json.loads((DATA / 'phase2-v1.json').read_text())
    cases = master['cases']
    if name == 'pilot-v1':
        pilot = json.loads((DATA / 'pilot-v1.json').read_text())
        mapping = {c['id']: c for c in cases}
        cases = [mapping[key] for key in pilot['case_ids']]
    elif name == 'natural-v1':
        cases = json.loads((DATA / 'natural-v1.json').read_text())['cases']
    ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)) or any(c['language'] not in ('es', 'en') for c in cases):
        raise EvaluationStopped('dataset_invalid')
    return cases, digest({'name': name, 'cases': cases, 'corpus': json.loads((DATA / 'corpus-v1.json').read_text())})
