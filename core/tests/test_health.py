from django.test import TestCase
from django.urls import reverse


class HealthTests(TestCase):
    def test_health_returns_database_status(self):
        response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['checks']['database'], 'ok')

    def test_home_renders(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'TRUEC@')
