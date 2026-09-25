from django.contrib.auth import get_user_model
from django.test import TestCase


class UserTests(TestCase):
    def test_user_uses_email_as_identifier(self):
        User = get_user_model()

        user = User.objects.create_user(
            email='persona@example.com',
            username='persona',
            password='secure-pass',
            first_name='Persona',
        )

        self.assertEqual(user.username, 'persona')
        self.assertEqual(user.email, 'persona@example.com')
        self.assertTrue(user.check_password('secure-pass'))
