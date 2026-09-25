from django.core.management.base import BaseCommand

from reservations.services import expire_due_reservations


class Command(BaseCommand):
    help = 'Marca como expiradas las reservas activas vencidas y libera los objetos que sigan publicables.'

    def handle(self, *args, **options):
        expired = expire_due_reservations()
        self.stdout.write(self.style.SUCCESS(f'Reservas expiradas: {expired}'))
