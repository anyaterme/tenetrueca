from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import UserCenterAccess
from catalog.models import Category, ReusableObject
from configuration.models import FunctionalRule
from core.roles import ROLE_DEFINITIONS, ROLE_OPERATOR, ROLE_SUPERVISOR
from locations.models import RecyclingCenter
from operations.models import Operation
from publications.models import Publication


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

DEMO_USERS = [
    ('Lucía', 'Hernández'),
    ('Mateo', 'González'),
    ('Sofía', 'Rodríguez'),
    ('Hugo', 'Pérez'),
    ('Martina', 'Martín'),
    ('Leo', 'García'),
    ('Valeria', 'Díaz'),
    ('Daniel', 'Santana'),
    ('Paula', 'Suárez'),
    ('Alejandro', 'Torres'),
    ('Emma', 'Cabrera'),
    ('Pablo', 'Ramos'),
    ('Carla', 'Medina'),
    ('Álvaro', 'Reyes'),
    ('Noa', 'Delgado'),
    ('Diego', 'Acosta'),
    ('Alba', 'Vega'),
    ('Javier', 'León'),
    ('Claudia', 'Marrero'),
    ('Marcos', 'Navarro'),
]

DEMO_CATEGORY_TREE = [
    (
        'deporte-y-ocio',
        'Deporte y ocio',
        [
            ('juegos-de-mesa', 'Juegos de Mesa'),
            ('instrumentos-musicales', 'Instrumentos Musicales'),
            ('libros-revistas-comics', 'Libros, revistas y comics'),
            ('videojuegos', 'Videojuegos'),
            ('musica', 'Música'),
            ('cine', 'Cine'),
            ('colecciones', 'Colecciones'),
            ('material-deportivo', 'Material deportivo'),
            ('otros-deporte-ocio', 'Otros'),
        ],
    ),
    (
        'ninos-y-bebes',
        'Niños y bebes',
        [
            ('juguetes', 'Juguetes'),
            ('accesorios-bebes', 'Accesorios de bebés'),
            ('carritos-sillas-muebles', 'Carritos, sillas, muebles'),
            ('otros-ninos-bebes', 'Otros'),
        ],
    ),
    (
        'hogar',
        'Hogar',
        [
            ('muebles', 'Muebles'),
            ('menaje', 'Menaje'),
            ('bricolaje-herramientas', 'Bricolaje - herramientas'),
            ('otros-hogar', 'Otros'),
        ],
    ),
]

OBJECT_VARIANTS = [
    ('Juego de ajedrez', 'juegos-de-mesa'),
    ('Guitarra acústica', 'instrumentos-musicales'),
    ('Colección de novelas', 'libros-revistas-comics'),
    ('Consola retro', 'videojuegos'),
    ('Tocadiscos portátil', 'musica'),
    ('Pack de películas', 'cine'),
    ('Álbum de sellos', 'colecciones'),
    ('Bicicleta urbana', 'material-deportivo'),
    ('Prismáticos', 'otros-deporte-ocio'),
    ('Juguete educativo', 'juguetes'),
    ('Mochila portabebé', 'accesorios-bebes'),
    ('Silla infantil', 'carritos-sillas-muebles'),
    ('Disfraz infantil', 'otros-ninos-bebes'),
    ('Mesa auxiliar', 'muebles'),
    ('Vajilla completa', 'menaje'),
    ('Set de herramientas', 'bricolaje-herramientas'),
    ('Lámpara de sobremesa', 'otros-hogar'),
]

LEGACY_DEMO_CATEGORY_SLUGS = {
    'objetos-reutilizables',
    'iluminacion',
    'libros',
    'deporte',
    'electrodomesticos',
    'textil',
    'bricolaje',
    'infantil',
    'electronica',
}


def demo_object_status(index):
    if index <= 90:
        return ReusableObject.Status.AVAILABLE
    if index <= 110:
        return ReusableObject.Status.RESERVED
    if index <= 125:
        return ReusableObject.Status.DELIVERED
    if index <= 140:
        return ReusableObject.Status.PENDING
    return ReusableObject.Status.REJECTED


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
                'username': 'ciudadano',
                'first_name': 'Ciudadano',
                'last_name': 'Demo',
                'account_status': User.AccountStatus.ACTIVE,
                'habitual_recycling_center': centers[0],
            },
        )
        if citizen.username != 'ciudadano':
            citizen.username = 'ciudadano'
            citizen.save(update_fields=['username'])
        citizen.groups.add(citizens_group)
        if not citizen.has_usable_password():
            citizen.set_password('demo12345')
            citizen.save(update_fields=['password'])

        operator, _ = User.objects.get_or_create(
            email='operador@tenetrueca.local',
            defaults={
                'username': 'operador',
                'first_name': 'Operador',
                'last_name': 'Demo',
                'account_status': User.AccountStatus.ACTIVE,
                'is_staff': True,
            },
        )
        if operator.username != 'operador':
            operator.username = 'operador'
            operator.save(update_fields=['username'])
        operator.groups.add(operator_group)
        UserCenterAccess.objects.get_or_create(user=operator, center=centers[0], role=UserCenterAccess.ScopeRole.OPERATOR)

        supervisor, _ = User.objects.get_or_create(
            email='supervisor@tenetrueca.local',
            defaults={
                'username': 'supervisor',
                'first_name': 'Supervisor',
                'last_name': 'Demo',
                'account_status': User.AccountStatus.ACTIVE,
                'is_staff': True,
            },
        )
        if supervisor.username != 'supervisor':
            supervisor.username = 'supervisor'
            supervisor.save(update_fields=['username'])
        supervisor.groups.add(supervisor_group)
        UserCenterAccess.objects.get_or_create(user=supervisor, center=centers[0], role=UserCenterAccess.ScopeRole.SUPERVISOR)

        demo_users = []
        for index, (first_name, last_name) in enumerate(DEMO_USERS, start=1):
            demo_user, created = User.objects.get_or_create(
                email=f'usuario{index:02d}@tenetrueca.local',
                defaults={
                    'username': f'usuario{index:02d}',
                    'first_name': first_name,
                    'last_name': last_name,
                    'account_status': User.AccountStatus.ACTIVE,
                    'habitual_recycling_center': centers[(index - 1) % len(centers)],
                },
            )
            if demo_user.username != f'usuario{index:02d}':
                demo_user.username = f'usuario{index:02d}'
                demo_user.save(update_fields=['username'])
            demo_user.groups.add(citizens_group)
            if created or not demo_user.password or not demo_user.has_usable_password():
                demo_user.set_password('demo12345')
                demo_user.save(update_fields=['password'])
            demo_users.append(demo_user)

        dani, dani_created = User.objects.get_or_create(
            email='dani@tenetrueca.local',
            defaults={
                'username': 'dani',
                'first_name': 'Dani',
                'last_name': 'Administrador',
                'account_status': User.AccountStatus.ACTIVE,
                'is_staff': True,
                'is_superuser': True,
            },
        )
        dani.username = 'dani'
        dani.first_name = 'Dani'
        dani.account_status = User.AccountStatus.ACTIVE
        dani.is_active = True
        dani.is_staff = True
        dani.is_superuser = True
        dani.save(update_fields=['username', 'first_name', 'account_status', 'is_active', 'is_staff', 'is_superuser'])
        if dani_created or not dani.password or not dani.has_usable_password():
            dani.set_password('dani')
            dani.save(update_fields=['password'])

        conditions = list(ReusableObject.Condition.values)
        seeded_objects = []
        now = timezone.now()
        category_nodes = {}
        for root_order, (root_slug, root_name, children) in enumerate(
            DEMO_CATEGORY_TREE,
            start=1,
        ):
            category_root, _ = Category.objects.update_or_create(
                slug=root_slug,
                defaults={
                    'name': root_name,
                    'description': f'Objetos de {root_name.lower()}.',
                    'parent': None,
                    'sort_order': root_order,
                    'is_active': True,
                },
            )
            for child_order, (child_slug, child_name) in enumerate(children, start=1):
                category_node, _ = Category.objects.update_or_create(
                    slug=child_slug,
                    defaults={
                        'name': child_name,
                        'parent': category_root,
                        'sort_order': child_order,
                        'is_active': True,
                    },
                )
                category_nodes[child_slug] = category_node

        for index in range(1, 151):
            base_title, category_slug = OBJECT_VARIANTS[(index - 1) % len(OBJECT_VARIANTS)]
            category_node = category_nodes[category_slug]
            category_name = category_node.name
            owner = demo_users[(index - 1) % len(demo_users)]
            title = f'{base_title} {index:03d}'
            description = f'Objeto de demostración {index:03d} para pruebas del catálogo TRUEC@.'
            object_status = demo_object_status(index)

            if object_status in {
                ReusableObject.Status.AVAILABLE,
                ReusableObject.Status.RESERVED,
                ReusableObject.Status.DELIVERED,
            }:
                publication_status = Publication.Status.APPROVED
            elif object_status == ReusableObject.Status.PENDING:
                publication_status = Publication.Status.PENDING_REVIEW
            else:
                publication_status = Publication.Status.REJECTED

            publication = (
                ReusableObject.objects.filter(reference=f'DEMO-OBJ-{index:04d}')
                .values_list('publication_id', flat=True)
                .first()
            )
            publication = Publication.objects.filter(pk=publication).first()
            if publication is None:
                publication = Publication.objects.filter(
                    submitter=owner,
                    description=description,
                ).first()
            if publication is None:
                publication = Publication(submitter=owner, title=title)

            publication.title = title
            publication.description = description
            publication.category = category_node
            publication.status = publication_status
            publication.submitted_at = now - timedelta(days=(150 - index) // 3, hours=2)
            publication.reviewed_at = (
                now - timedelta(days=(150 - index) // 3, hours=1)
                if publication_status != Publication.Status.PENDING_REVIEW
                else None
            )
            publication.approved_at = (
                now - timedelta(days=(150 - index) // 3)
                if publication_status == Publication.Status.APPROVED
                else None
            )
            publication.status_changed_at = now - timedelta(days=(150 - index) // 3)
            publication.save()

            is_validated = publication_status == Publication.Status.APPROVED
            reusable_object, _ = ReusableObject.objects.update_or_create(
                reference=f'DEMO-OBJ-{index:04d}',
                defaults={
                    'owner': owner,
                    'center': centers[(index - 1) % len(centers)],
                    'title': title,
                    'description': description,
                    'category': category_name,
                    'category_node': category_node,
                    'publication': publication if is_validated else None,
                    'condition': conditions[(index - 1) % len(conditions)],
                    'status': object_status,
                    'points_cost': 5 + ((index - 1) % 10) * 5,
                    'is_pack': index % 12 == 0,
                    'weight_kg': Decimal('0.75') + Decimal(index % 24) / Decimal('2'),
                    'dimensions': {
                        'width_cm': 20 + index % 60,
                        'height_cm': 15 + index % 80,
                        'depth_cm': 10 + index % 45,
                    },
                    'validated_at': publication.approved_at if is_validated else None,
                    'validated_by': supervisor if is_validated else None,
                    'validation_notes': 'Validación automática del conjunto de demostración.' if is_validated else '',
                    'created_at': now - timedelta(days=(150 - index) // 3),
                },
            )
            seeded_objects.append(reusable_object)

        Category.objects.filter(slug__in=LEGACY_DEMO_CATEGORY_SLUGS).update(is_active=False)

        operation_types = list(Operation.Type.values)
        operation_statuses = list(Operation.Status.values)
        for index in range(1, 76):
            reusable_object = seeded_objects[((index - 1) * 2) % len(seeded_objects)]
            Operation.objects.update_or_create(
                reference=f'DEMO-OP-{index:04d}',
                defaults={
                    'operation_type': operation_types[(index - 1) % len(operation_types)],
                    'status': operation_statuses[(index - 1) % len(operation_statuses)],
                    'actor': demo_users[(index - 1) % len(demo_users)],
                    'reusable_object': reusable_object,
                    'center': reusable_object.center,
                    'occurred_at': now - timedelta(days=(75 - index) // 2, hours=index % 24),
                    'notes': f'Operación de demostración {index:03d}.',
                    'metadata': {
                        'dataset': 'release-demo',
                        'sequence': index,
                        'idempotency_key': f'demo-operation-{index:04d}',
                    },
                },
            )

        if options['create_superuser']:
            admin, created = User.objects.get_or_create(
                email=options['superuser_email'],
                defaults={
                    'username': options['superuser_email'].split('@', 1)[0],
                    'first_name': 'Admin',
                    'account_status': User.AccountStatus.ACTIVE,
                    'is_staff': True,
                    'is_superuser': True,
                },
            )
            if created or not admin.has_usable_password():
                admin.set_password(options['superuser_password'])
                admin.save(update_fields=['password'])

        self.stdout.write(
            self.style.SUCCESS(
                'Datos demo creados o actualizados: 20 usuarios, 150 objetos y 75 operaciones.'
            )
        )
