from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q

from assistant.models import PublicationPreparation
from assistant.preparations import discard_preparation


class Command(BaseCommand):
    help = 'Caduca preparaciones abandonadas y limpia fotos temporales sin borrar fotos enviadas.'

    def handle(self, *args, **options):
        count = 0
        candidates = PublicationPreparation.objects.filter(expires_at__lte=timezone.now()).filter(
            Q(status='open') | Q(photos__isnull=False),
        ).distinct().values_list('pk', flat=True)
        for preparation_id in candidates.iterator():
            discard_preparation(preparation_id, expired=True)
            count += 1
        self.stdout.write(f'Preparaciones limpiadas: {count}.')
