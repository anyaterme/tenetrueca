"""Render the real portal/widget against an ephemeral SQLite database, never local data.

Run only with tenetrueca.test_settings and an output directory in /tmp.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'tenetrueca.test_settings'
os.environ['CI'] = 'true'
import django

django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import translation
from faq.models import FAQ

assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
assert settings.DATABASES['default']['NAME'] == ':memory:'
output = pathlib.Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
call_command('migrate', run_syncdb=True, verbosity=0)
faq = FAQ.objects.create(question='¿Cómo publicar un objeto?', answer='Inicia sesión, completa los datos y añade fotografías para presentar la propuesta.')
with override_settings(CHATBOT_ENABLED=True):
    for language in ('es', 'en'):
        for authenticated in (False, True):
            client = Client()
            if authenticated:
                user = get_user_model().objects.create_user(email=f'browser-{language}@example.invalid')
                client.force_login(user)
            with translation.override(language):
                response = client.get('/')
            assert response.status_code == 200
            name = f'{language}-{"user" if authenticated else "anon"}.html'
            (output / name).write_bytes(response.content)
    (output / 'faq.html').write_bytes(Client().get('/faq/').content)
(output / 'manifest.json').write_text(json.dumps({'faq_url': f'/faq/#faq-question-{faq.pk}'}))
print('Rendered four portal fixtures and FAQ; synthetic database only.')
