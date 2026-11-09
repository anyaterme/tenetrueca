"""Disabled by default; explicit future authorization cannot bypass persistent limits."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from chatbot.evaluation.errors import EvaluationStopped
from chatbot.evaluation.output import atomic_json
from chatbot.evaluation.runner import evaluate
from chatbot.safety import ProviderError


class Command(BaseCommand):
    help = 'Preview a chatbot evaluation. Real calls require --allow-live AND independent staging authorization.'

    def add_arguments(self, parser):
        parser.add_argument('--allow-live', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--resume', action='store_true')
        parser.add_argument('--run-id', default=None)
        parser.add_argument('--dataset', choices=['pilot-v1', 'phase2-v1'], default='pilot-v1')
        parser.add_argument('--case-ids', nargs='+', default=None,
                            help='Ordered IDs from the dataset. Live selection must match CHATBOT_EVAL_CASE_IDS exactly.')
        parser.add_argument('--model', default=settings.CHATBOT_OPENAI_MODEL)
        parser.add_argument('--mode', choices=['extractive', 'grounded', 'both'], default='both')
        parser.add_argument('--max-requests', type=int, default=settings.CHATBOT_EVAL_MAX_REQUESTS)
        parser.add_argument('--max-budget-usd', default=str(settings.CHATBOT_EVAL_MAX_BUDGET_USD))
        parser.add_argument('--max-input-tokens', type=int, default=settings.CHATBOT_EVAL_MAX_INPUT_TOKENS)
        parser.add_argument('--max-output-tokens', type=int, default=settings.CHATBOT_EVAL_MAX_OUTPUT_TOKENS)
        parser.add_argument('--output')

    def handle(self, *args, **options):
        try:
            report = evaluate(options)
            if options['output']:
                atomic_json(options['output'], report)
                self.stdout.write('Evaluation report written. The database ledger remains authoritative.')
            else:
                self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        except (EvaluationStopped, ProviderError) as error:
            raise CommandError(f'Evaluation stopped: {error.code}; no automatic retry.') from None
        except (OSError, ValueError):
            raise CommandError('Evaluation export failed; persisted reservations/results were not reset.') from None
        except DatabaseError:
            raise CommandError('Evaluation storage failed; reservations must be recovered, never reset.') from None
        except Exception:
            raise CommandError('Evaluation interrupted; inspect the persistent ledger before an explicit resume.') from None
