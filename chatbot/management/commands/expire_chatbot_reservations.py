from django.core.management.base import BaseCommand

from chatbot.services import reap_expired_reservations


class Command(BaseCommand):
    help = 'Release expired chatbot reservations after interrupted requests (no provider calls).'

    def handle(self, *args, **options):
        self.stdout.write(f'Released {reap_expired_reservations()} expired chatbot reservation(s).')
