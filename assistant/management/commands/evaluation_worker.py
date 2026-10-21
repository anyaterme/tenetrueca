import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection
from django.utils import timezone

from assistant.models import PublicationEvaluation
from assistant.services import expire_interrupted_jobs, process_evaluation


class Command(BaseCommand):
    help = 'Procesa la cola persistente de evaluaciones, sin bloquear peticiones HTTP.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Procesa como máximo un trabajo.')
        parser.add_argument('--poll-seconds', type=float, default=3)

    def handle(self, *args, **options):
        if options['poll_seconds'] < 1:
            raise CommandError('El intervalo mínimo es un segundo.')
        try:
            while True:
                if not connection.in_atomic_block:
                    close_old_connections()
                expire_interrupted_jobs()
                candidate = PublicationEvaluation.objects.filter(
                    status='pending', origin='review', is_manual=False, available_at__lte=timezone.now(),
                ).order_by('created_at', 'pk').values_list('pk', flat=True).first()
                if candidate:
                    process_evaluation(candidate)
                if options['once']:
                    return
                if candidate is None:
                    time.sleep(options['poll_seconds'])
        except KeyboardInterrupt:
            self.stdout.write('Worker detenido. Los trabajos interrumpidos no se reintentan automáticamente.')
