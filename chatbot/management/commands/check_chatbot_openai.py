"""Retired unbudgeted smoke entry point: route operators to the persistent evaluator."""
from django.core.management.base import BaseCommand, CommandError

from chatbot.safety import ProviderError, require_openai_access


class Command(BaseCommand):
    help = 'Retired: use evaluate_chatbot_live, which defaults to dry-run and enforces a persistent budget.'

    def add_arguments(self, parser):
        parser.add_argument('--allow-live', action='store_true')
        parser.add_argument('--language', choices=['es', 'en'], default='es')
        parser.add_argument('--question', default='¿Cómo funciona TRUEC@?')

    def handle(self, *args, **options):
        if not options['allow_live']:
            raise CommandError('No request sent. Use evaluate_chatbot_live (dry-run by default).')
        try:
            require_openai_access()  # Never weaken the shared test/CI guard.
        except ProviderError as error:
            raise CommandError(f'Chatbot check blocked ({error.code}); no request sent.') from None
        raise CommandError('Unbudgeted smoke checks are retired. Use evaluate_chatbot_live with its budget authorization; no request sent.')
