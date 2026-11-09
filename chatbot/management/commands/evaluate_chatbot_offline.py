"""Isolated DB only: reproducible mock comparison/performance measurements."""
import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import setup_databases, teardown_databases
from django.test import override_settings

from chatbot.evaluation.errors import EvaluationStopped
from chatbot.evaluation.offline import evaluate_dataset, benchmark, history_probe
from chatbot.evaluation.output import atomic_json
from assistant.safety import is_test_process


class Command(BaseCommand):
    help = 'Evaluate 90 synthetic cases in a NEW isolated test DB, with mocks and no SDK calls.'

    def add_arguments(self, parser):
        parser.add_argument('--dataset', choices=['phase2-v1', 'pilot-v1'], default='phase2-v1')
        parser.add_argument('--samples', type=int, default=20)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        module = os.environ.get('DJANGO_SETTINGS_MODULE', '')
        if module not in ('tenetrueca.test_settings', 'tenetrueca.test_postgres_settings') or not is_test_process():
            raise CommandError('Synthetic evaluation requires explicit isolated test settings and CI/test network guards.')
        if not 1 <= options['samples'] <= 100:
            raise CommandError('samples must be 1..100')
        databases = setup_databases(verbosity=0, interactive=False)
        try:
            with override_settings(CHATBOT_ENABLED=True, CHATBOT_PROVIDER='mock', OPENAI_API_KEY='',
                                   CHATBOT_RATE_LIMIT_REQUESTS=100000):
                report = evaluate_dataset(options['dataset'])
                report['performance'] = benchmark(options['samples'])
                report['history_scaling'] = history_probe(options['samples'])
                if connection.vendor == 'postgresql':
                    from chatbot.knowledge.sources.faq import _postgres_candidates
                    from chatbot.knowledge.normalization import normalize_query
                    from chatbot.knowledge.registry import SOURCES
                    from faq.models import FAQ
                    rows = FAQ.objects.filter(is_published=True).values(*SOURCES['faq'].fields)
                    query = normalize_query('publicar objeto', language='es', max_chars=2000)
                    report['postgres_fts_explain'] = json.loads(_postgres_candidates(rows, query).explain(format='json', analyze=True))
                atomic_json(options['output'], report)
            self.stdout.write(json.dumps({'backend': report['backend'], 'summary': report['summary'], 'real_openai_calls': 0}))
        finally:
            teardown_databases(databases, verbosity=0)
