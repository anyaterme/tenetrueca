from django.core.exceptions import ValidationError
from django.contrib import admin
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from catalog.models import Category


class CategoryModelTests(TestCase):
    def test_category_supports_hierarchy_and_protects_parent(self):
        parent = Category.objects.create(name='Hogar', slug='hogar')
        child = Category.objects.create(name='Muebles', slug='muebles', parent=parent)

        self.assertEqual(list(parent.children.all()), [child])
        with self.assertRaises(ProtectedError):
            parent.delete()

    def test_category_rejects_cycles(self):
        parent = Category.objects.create(name='Hogar', slug='hogar')
        child = Category.objects.create(name='Muebles', slug='muebles', parent=parent)
        parent.parent = child

        with self.assertRaises(ValidationError):
            parent.full_clean()

    def test_category_slug_is_unique_case_insensitively(self):
        Category.objects.create(name='Hogar', slug='hogar')

        with self.assertRaises(ValidationError):
            Category.objects.create(name='Hogar duplicado', slug='HOGAR')

    def test_category_can_be_deactivated_and_is_registered_in_admin(self):
        category = Category.objects.create(name='Hogar', slug='hogar')
        category.is_active = False
        category.save()

        self.assertFalse(category.is_active)
        self.assertTrue(admin.site.is_registered(Category))
