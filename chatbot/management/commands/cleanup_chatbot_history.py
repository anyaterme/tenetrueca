import json

from django.core.management.base import BaseCommand, CommandError

from chatbot.retention import cleanup_history


class Command(BaseCommand):
    help = 'Preview chatbot retention (default 30 days). Deletion requires explicit --execute.'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, **options):
        try:
            result = cleanup_history(execute=options['execute'], batch_size=options['batch_size'])
        except ValueError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(result, sort_keys=True))
