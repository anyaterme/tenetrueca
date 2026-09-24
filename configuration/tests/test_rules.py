from django.test import TestCase

from configuration.models import FunctionalRule


class FunctionalRuleTests(TestCase):
    def test_rule_versions_are_unique_per_key(self):
        FunctionalRule.objects.create(
            key='limite-reservas',
            name='Limite reservas',
            value_type=FunctionalRule.ValueType.INTEGER,
            value=3,
            version=1,
        )

        self.assertTrue(FunctionalRule.objects.filter(key='limite-reservas', version=1).exists())
