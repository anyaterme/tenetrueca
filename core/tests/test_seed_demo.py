from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from catalog.models import Category, ReusableObject
from inventory.models import InventoryItem
from operations.models import Operation
from publications.models import Publication


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SeedDemoTests(TestCase):
    def test_seed_creates_requested_dataset_and_is_idempotent(self):
        call_command('seed_demo', verbosity=0)
        call_command('seed_demo', verbosity=0)

        User = get_user_model()
        self.assertEqual(User.objects.filter(email__startswith='usuario', email__endswith='@tenetrueca.local').count(), 20)
        self.assertEqual(ReusableObject.objects.filter(reference__startswith='DEMO-OBJ-').count(), 150)
        self.assertEqual(Operation.objects.filter(reference__startswith='DEMO-OP-').count(), 75)
        self.assertEqual(Publication.objects.count(), 150)
        self.assertEqual(InventoryItem.objects.public_catalog().count(), 90)

        expected_categories = {
            'Deporte y ocio': [
                'Juegos de Mesa',
                'Instrumentos Musicales',
                'Libros, revistas y comics',
                'Videojuegos',
                'Música',
                'Cine',
                'Colecciones',
                'Material deportivo',
                'Otros',
            ],
            'Niños y bebes': [
                'Juguetes',
                'Accesorios de bebés',
                'Carritos, sillas, muebles',
                'Otros',
            ],
            'Hogar': [
                'Muebles',
                'Menaje',
                'Bricolaje - herramientas',
                'Otros',
            ],
        }
        active_roots = Category.objects.filter(parent__isnull=True, is_active=True)
        self.assertEqual(list(active_roots.values_list('name', flat=True)), list(expected_categories))
        self.assertEqual(Category.objects.filter(parent__isnull=False, is_active=True).count(), 17)
        for root_name, expected_children in expected_categories.items():
            root = active_roots.get(name=root_name)
            children = root.children.filter(is_active=True)
            self.assertEqual(list(children.values_list('name', flat=True)), expected_children)
            for child in children:
                self.assertTrue(
                    ReusableObject.objects.filter(
                        reference__startswith='DEMO-OBJ-',
                        category_node=child,
                    ).exists()
                )

        self.assertFalse(
            Category.objects.filter(
                slug='objetos-reutilizables',
                is_active=True,
            ).exists()
        )

        public_object = InventoryItem.objects.public_catalog().first()
        self.assertIsNotNone(public_object.publication)
        self.assertIsNotNone(public_object.category_node)
        self.assertIsNotNone(public_object.validated_at)
        self.assertIsNotNone(public_object.validated_by)

        dani = User.objects.get(email='dani@tenetrueca.local')
        self.assertTrue(dani.is_superuser)
        self.assertTrue(dani.is_staff)
        self.assertEqual(dani.username, 'dani')
        self.assertTrue(dani.check_password('dani'))

        operation = Operation.objects.filter(reference__startswith='DEMO-OP-').first()
        self.assertIsNotNone(operation.actor)
        self.assertIsNotNone(operation.reusable_object)
        self.assertIsNotNone(operation.center)
