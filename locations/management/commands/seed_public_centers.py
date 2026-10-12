import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.models import AuditEvent
from locations.models import RecyclingCenter


class Command(BaseCommand):
    help = 'Carga los ocho puntos limpios del Cabildo sin sustituir datos personalizados.'

    def add_arguments(self, parser):
        parser.add_argument('--publish', action='store_true', help='Publica los ocho centros en el mapa.')

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(__file__).resolve().parents[2] / 'data' / 'tenerife_centers.json'
        dataset = json.loads(path.read_text())
        aliases = {
            'punto-limpio-jagua': ('punto-limpio-santa-cruz',),
            'punto-limpio-taco': ('punto-limpio-la-laguna',),
            'punto-limpio-guimar': ('punto-limpio-güimar',),
        }
        for item in dataset['centers']:
            center = RecyclingCenter.objects.filter(slug=item['slug']).first()
            if center is None:
                candidates = RecyclingCenter.objects.filter(slug__in=aliases.get(item['slug'], ()))
                for candidate in candidates:
                    # Generic demo centers can be reconciled while retaining their IDs and relations.
                    if item['slug'] == 'punto-limpio-guimar' or (candidate.address in ('', 'Tenerife') and not candidate.has_coordinates):
                        center = candidate
                        break
            if center is None:
                center = RecyclingCenter.objects.filter(name=item['name']).first()
            created = center is None
            if created:
                center = RecyclingCenter(slug=item['slug'], name=item['name'])
            before = None if created else {
                'name': center.name, 'address': center.address,
                'latitude': str(center.latitude) if center.latitude is not None else None,
                'longitude': str(center.longitude) if center.longitude is not None else None,
                'show_on_public_map': center.show_on_public_map,
            }
            if created or (center.address in ('', 'Tenerife') and not center.has_coordinates):
                center.name = item['name']
                center.address = item['address']
            if center.latitude is None:
                center.latitude = Decimal(item['latitude'])
            if center.longitude is None:
                center.longitude = Decimal(item['longitude'])
            if not center.phone:
                center.phone = item['phone']
            if center.schedule in ('', 'Lunes a sabado'):
                center.schedule = item['schedule']
            if options['publish']:
                center.show_on_public_map = True
            try:
                center.full_clean()
            except Exception as exc:
                raise CommandError(f'Revisa los datos de {center.name}: {exc}') from exc
            after = {
                'name': center.name, 'address': center.address,
                'latitude': str(center.latitude), 'longitude': str(center.longitude),
                'show_on_public_map': center.show_on_public_map,
            }
            center.save()
            if before != after:
                AuditEvent.objects.create(
                    action='center.imported', entity='RecyclingCenter', entity_id=str(center.pk),
                    source='seed_public_centers', before=before, after=after,
                    metadata={'source': dataset['source'], 'source_updated': dataset['source_updated']},
                )
            self.stdout.write(f'{center.name}: {"creado" if created else "actualizado"}.')
