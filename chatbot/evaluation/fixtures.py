"""Synthetic corpus installer, callable only inside a Django isolated test database."""
import json

from django.db import connection
from assistant.safety import is_test_process
from catalog.models import Category
from faq.models import FAQ
from locations.models import RecyclingCenter
from .dataset import DATA
from .errors import EvaluationStopped


def assert_synthetic_database():
    name = str(connection.settings_dict['NAME'])
    if not is_test_process() or not (name == ':memory:' or name.startswith(('file:memorydb_', 'test_'))):
        raise EvaluationStopped('synthetic_database_required')


def install_case(case):
    assert_synthetic_database()
    # Exclusively ephemeral fixtures. Never invoked by evaluate_chatbot_live.
    FAQ.objects.all().delete()
    RecyclingCenter.objects.all().delete()
    Category.objects.update(parent=None)  # Only this guarded, freshly created test database.
    Category.objects.all().delete()
    corpus = json.loads((DATA / 'corpus-v1.json').read_text())['faqs']
    mapping = {}
    for key in case['fixture_ids']:
        faq = FAQ.objects.create(**corpus[key])
        mapping[key] = f'faq:{faq.pk}'
    if case['center_fixture']:
        variant = case['center_fixture']
        center = RecyclingCenter.objects.create(name='Punto Limpio Oculto' if variant == 'hidden' else 'Punto Limpio de Adeje',
            address='PRIVATE-HIDDEN-CENTER' if variant == 'hidden' else 'Dirección pública sintética de Adeje',
            latitude='28.123456', longitude='-16.123456', phone='922 000 000',
            schedule='' if variant == 'empty' else 'Lunes de 09:00 a 14:00', is_active=True,
            show_on_public_map=variant != 'hidden', email='PRIVATE-CENTER@example.invalid', services=['PRIVATE-SERVICE'])
        mapping['center.adeje'] = f'center:{center.pk}'
    if 'category.sports' in case['expected_sources']:
        category = Category.objects.create(name='Deporte', slug='deporte')
        mapping['category.sports'] = f'category:{category.slug}'
        ancestor = Category.objects.create(name='PRIVATE-ANCESTOR', slug='hidden-parent', is_active=False)
        Category.objects.create(name='PRIVATE-CHILD', slug='hidden-child', parent=ancestor)
    return {s: mapping.get(s, s) for s in case['expected_sources']}
