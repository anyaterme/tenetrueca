"""Reusable offline responses. Mock mode never claims to have examined photographs."""
import json
from pathlib import Path


def simulated_response(scenario='unverifiable'):
    path = Path(__file__).parent / 'fixtures' / f'{scenario}.json'
    return json.loads(path.read_text())
