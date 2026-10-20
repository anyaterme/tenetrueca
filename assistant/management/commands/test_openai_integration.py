from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from assistant.models import PublicationEvaluation
from assistant.safety import LiveCallsDisabled, require_live_access
from assistant.services import enqueue_evaluation, process_evaluation
from publications.models import Publication


class Command(BaseCommand):
    help = ('Prueba MANUAL excepcional: una solicitud real con una foto pequeña. '
            'Requiere autorización previa del usuario y --authorize-cost.')

    def add_arguments(self, parser):
        parser.add_argument('publication_id', type=int)
        parser.add_argument('--authorize-cost', action='store_true')
        parser.add_argument('--budget-usd', required=True)

    def handle(self, *args, **options):
        self.stdout.write('Se utilizará la API de OpenAI con coste para comprobar Responses API, '
                          'una fotografía y la validación de la evaluación. Solicitudes: máximo 1; '
                          'sin reintentos; foto: 512 px, detalle bajo; salida: máximo 1200 tokens.')
        if not options['authorize_cost']:
            raise CommandError('Solicita autorización al usuario antes de usar --authorize-cost.')
        try:
            require_live_access()
        except LiveCallsDisabled as error:
            raise CommandError(str(error)) from error
        if settings.OPENAI_EVALUATION_BACKEND != 'live':
            raise CommandError('Activa explícitamente OPENAI_EVALUATION_BACKEND=live.')
        try:
            budget = Decimal(options['budget_usd'])
        except InvalidOperation as error:
            raise CommandError('Presupuesto no válido.') from error
        if not budget.is_finite() or budget <= 0 or budget > Decimal('1'):
            raise CommandError('El presupuesto debe ser positivo y no superar 1 USD.')
        if settings.OPENAI_REQUEST_RESERVE_USD > budget:
            raise CommandError('La reserva configurada supera el presupuesto autorizado.')
        try:
            publication = Publication.objects.get(pk=options['publication_id'])
        except Publication.DoesNotExist as error:
            raise CommandError('No existe esa publicación.') from error
        if not publication.photos.exists():
            raise CommandError('Se necesita una fotografía.')
        if publication.ai_evaluations.filter(is_simulated=False).exists():
            raise CommandError('Ya se solicitó un análisis real para esta publicación. Revisa su historial.')
        if publication.ai_evaluations.filter(status__in=['pending', 'processing']).exists():
            raise CommandError('Hay un trabajo en curso. Detén y revisa la cola antes de la prueba manual.')
        evaluation, _ = enqueue_evaluation(publication.pk, force=True, manual=True)
        process_evaluation(evaluation.pk, manual=True, budget=budget,
                           max_side=512, max_output_tokens=1200)
        evaluation.refresh_from_db()
        calls = evaluation.usage_attempts.filter(is_simulated=False).count()
        self.stdout.write(f'Solicitudes reservadas: {calls}; entrada: {evaluation.input_tokens}; '
                          f'salida: {evaluation.output_tokens}; '
                          f'coste estimado: {evaluation.estimated_cost_usd} USD; '
                          f'estado: {evaluation.status}.')
        if evaluation.status != PublicationEvaluation.Status.COMPLETED:
            raise CommandError(f'La prueba no se completó ({evaluation.error_code}). '
                               'El consumo de errores sin respuesta puede ser desconocido. No repitas automáticamente.')
