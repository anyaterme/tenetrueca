from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import UserCenterAccess
from configuration.models import FunctionalRule
from core.roles import ROLE_DEFINITIONS, ROLE_OPERATOR, ROLE_SUPERVISOR
from locations.models import RecyclingCenter


CENTERS = [
    ('punto-limpio-adeje', 'Punto Limpio de Adeje'),
    ('punto-limpio-arona', 'Punto Limpio de Arona'),
    ('punto-limpio-güimar', 'Punto Limpio de Guimar'),
    ('punto-limpio-icod', 'Punto Limpio de Icod de los Vinos'),
    ('punto-limpio-la-laguna', 'Punto Limpio de La Laguna'),
    ('punto-limpio-la-orotava', 'Punto Limpio de La Orotava'),
    ('punto-limpio-santa-cruz', 'Punto Limpio de Santa Cruz'),
    ('punto-limpio-tacoronte', 'Punto Limpio de Tacoronte'),
]

RULES = [
    ('puntos-registro', 'Puntos por registro', FunctionalRule.ValueType.INTEGER, 20),
    ('puntos-deposito-base', 'Puntos base por deposito aceptado', FunctionalRule.ValueType.INTEGER, 10),
    ('coste-recogida-base', 'Coste base de recogida', FunctionalRule.ValueType.INTEGER, 5),
    ('limite-publicaciones-diarias', 'Limite diario de publicaciones', FunctionalRule.ValueType.INTEGER, 5),
    ('limite-reservas-activas', 'Limite de reservas activas', FunctionalRule.ValueType.INTEGER, 3),
    ('plazo-recogida-dias', 'Plazo de recogida en dias', FunctionalRule.ValueType.INTEGER, 7),
]


class Command(BaseCommand):
    help = 'Crea datos demo idempotentes para desarrollo.'

    def add_arguments(self, parser):
        parser.add_argument('--create-superuser', action='store_true')
        parser.add_argument('--superuser-email', default='admin@tenetrueca.local')
        parser.add_argument('--superuser-password', default='admin12345')

    @transaction.atomic
    def handle(self, *args, **options):
        for role in ROLE_DEFINITIONS:
            Group.objects.get_or_create(name=role.name)

        centers = []
        for slug, name in CENTERS:
            center, _ = RecyclingCenter.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': name,
                    'address': 'Tenerife',
                    'schedule': 'Lunes a sabado',
                    'services': ['recepcion', 'recogida'],
                },
            )
            centers.append(center)

        for key, name, value_type, value in RULES:
            FunctionalRule.objects.get_or_create(
                key=key,
                version=1,
                defaults={
                    'name': name,
                    'value_type': value_type,
                    'value': value,
                },
            )

        User = get_user_model()
        citizens_group = Group.objects.get(name='Usuario registrado')
        operator_group = Group.objects.get(name=ROLE_OPERATOR)
        supervisor_group = Group.objects.get(name=ROLE_SUPERVISOR)

        citizen, _ = User.objects.get_or_create(
            email='ciudadano@tenetrueca.local',
            defaults={
                'first_name': 'Ciudadano',
                'last_name': 'Demo',
                'account_status': User.AccountStatus.ACTIVE,
                'habitual_recycling_center': centers[0],
            },
        )
        citizen.groups.add(citizens_group)
        if not citizen.has_usable_password():
            citizen.set_password('demo12345')
            citizen.save(update_fields=['password'])

        operator, _ = User.objects.get_or_create(
            email='operador@tenetrueca.local',
            defaults={
                'first_name': 'Operador',
                'last_name': 'Demo',
                'account_status': User.AccountStatus.ACTIVE,
                'is_staff': True,
            },
        )
        operator.groups.add(operator_group)
        UserCenterAccess.objects.get_or_create(user=operator, center=centers[0], role=UserCenterAccess.ScopeRole.OPERATOR)

        supervisor, _ = User.objects.get_or_create(
            email='supervisor@tenetrueca.local',
            defaults={
                'first_name': 'Supervisor',
                'last_name': 'Demo',
                'account_status': User.AccountStatus.ACTIVE,
                'is_staff': True,
            },
        )
        supervisor.groups.add(supervisor_group)
        UserCenterAccess.objects.get_or_create(user=supervisor, center=centers[0], role=UserCenterAccess.ScopeRole.SUPERVISOR)

        if options['create_superuser']:
            admin, created = User.objects.get_or_create(
                email=options['superuser_email'],
                defaults={
                    'first_name': 'Admin',
                    'account_status': User.AccountStatus.ACTIVE,
                    'is_staff': True,
                    'is_superuser': True,
                },
            )
            if created or not admin.has_usable_password():
                admin.set_password(options['superuser_password'])
                admin.save(update_fields=['password'])

        self.stdout.write(self.style.SUCCESS('Datos demo creados o actualizados.'))
