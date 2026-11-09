"""Isolation checks without touching Docker, operational settings or external services."""
from copy import deepcopy
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from chatbot.staging_isolation import validate


def compose():
    return {'name': 'trueca-chatbot-validation-test',
        'services': {
            'app': {'networks': {'staging_private': {}}, 'user': '10001:10001', 'read_only': True,
                    'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                    'environment': {'DJANGO_SETTINGS_MODULE': 'tenetrueca.staging_settings',
                        'CHATBOT_PROVIDER': 'mock', 'CHATBOT_EVAL_ALLOW_LIVE': 'False',
                        'OPENAI_API_KEY': '', 'OPENAI_LIVE_ENABLED': 'False'},
                    'volumes': [{'type': 'volume', 'source': 'staging_static'}]},
            'staging_db': {'networks': {'staging_private': {}}, 'volumes': []},
            'proxy': {'networks': {'staging_private': {}, 'staging_ingress': {}},
                      'user': '101:101', 'read_only': True,
                      'ports': [{'host_ip': '127.0.0.1', 'published': '8083'}]}},
        'networks': {'staging_private': {'internal': True, 'name': 'trueca-chatbot-validation-test_staging_private'},
                     'staging_ingress': {'internal': False, 'name': 'trueca-chatbot-validation-test_staging_ingress'}},
        'volumes': {'staging_static': {'name': 'trueca-chatbot-validation-test_staging_static'}},
        'secrets': {'django_secret_key': {'file': '/tmp/new-private-staging-secret'}}}


class StagingIsolationTests(SimpleTestCase):
    def test_separate_compose_contract_is_accepted(self):
        volumes, networks = validate(compose(), ephemeral=True)
        self.assertTrue(volumes)
        self.assertTrue(networks)

    def test_operational_resources_public_network_ports_and_source_mounts_are_rejected(self):
        changes = (
            lambda c: c.update(name='tenetrueca'),
            lambda c: c['services'].update(evaluation_worker={}),
            lambda c: c['networks']['staging_private'].update(internal=False),
            lambda c: c['services']['app'].update(container_name='tenetrueca_app'),
            lambda c: c['services']['proxy']['ports'][0].update(host_ip='0.0.0.0'),
            lambda c: c['services']['proxy']['ports'][0].update(published='8082'),
            lambda c: c['services']['app'].update(ports=[{'published': '8084'}]),
            lambda c: c['services']['proxy'].update(secrets=[{'source': 'postgres_password'}]),
            lambda c: c['services']['app'].update(volumes=[{'type': 'bind', 'source': '../app'}]),
            lambda c: c['services']['staging_db'].update(ports=[{'published': '5432'}]),
            lambda c: c['services']['staging_db'].update(volumes=[{'type': 'volume', 'source': 'staging_db'}]),
            lambda c: c['volumes']['staging_static'].update(name='tenetrueca_static'),
            lambda c: c['volumes']['staging_static'].update(external=True),
            lambda c: c['services']['app'].update(tmpfs=['/tmp:uid=10001', 'mode=1777']),
            lambda c: c['secrets']['django_secret_key'].update(file=str(Path(__file__).resolve().parents[2] / '.env')),
        )
        for change in changes:
            candidate = deepcopy(compose()); change(candidate)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError):
                validate(candidate, ephemeral=True)

    def test_ai_activation_and_weak_container_permissions_are_rejected(self):
        for fields in ({'CHATBOT_PROVIDER': 'openai'}, {'CHATBOT_EVAL_ALLOW_LIVE': 'True'},
                       {'OPENAI_API_KEY': 'not-a-real-key'}, {'OPENAI_LIVE_ENABLED': 'True'}):
            candidate = compose(); candidate['services']['app']['environment'].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate(candidate)
        candidate = compose(); candidate['services']['app']['read_only'] = False
        with self.assertRaises(ValueError):
            validate(candidate)

    def test_staging_commands_refuse_normal_settings_or_missing_ephemeral_authorization(self):
        with self.assertRaises(CommandError):
            call_command('check_chatbot_staging', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('validate_chatbot_staging', stdout=StringIO())

    def test_staging_profile_skips_local_secrets_and_rejects_accidental_live_flags(self):
        program = '''
import os,sys,types
from pathlib import Path
from unittest.mock import patch
fake=types.ModuleType('tenetrueca.local_settings')
fake.SECRET_KEY='OPERATIONAL-SECRET-SENTINEL'
fake.DATABASES={'default': {'ENGINE':'django.db.backends.sqlite3','NAME':'OPERATIONAL-DB'}}
sys.modules['tenetrueca.local_settings']=fake
original=Path.read_text
def read(self,*args,**kwargs):
    return 'synthetic-independent-secret-'*4 if str(self.parent)=='/run/secrets' else original(self,*args,**kwargs)
with patch.object(Path,'read_text',read):
    import django
    django.setup()
from django.conf import settings
assert settings.SECRET_KEY != fake.SECRET_KEY
assert settings.DATABASES['default']['HOST']=='staging_db'
assert settings.SESSION_COOKIE_NAME=='trueca_staging_sessionid'
assert settings.OPENAI_API_KEY=='' and settings.CHATBOT_PROVIDER=='mock'
assert settings.CHATBOT_ENABLED is False
assert str(settings.CHATBOT_EVAL_MAX_BUDGET_USD)=='0.25'
assert settings.CHATBOT_EVAL_MAX_REQUESTS==7 and settings.CHATBOT_EVAL_MAX_OUTPUT_TOKENS==300
print('isolated')
'''
        baseline = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'tenetrueca.staging_settings',
                    'CHATBOT_PROVIDER': 'mock', 'CHATBOT_EVAL_ALLOW_LIVE': 'False',
                    'CHATBOT_ENABLED': 'False', 'CHATBOT_EVAL_MAX_BUDGET_USD': '0.25',
                    'CHATBOT_EVAL_MAX_REQUESTS': '7', 'CHATBOT_EVAL_MAX_OUTPUT_TOKENS': '300',
                    'TRUECA_STAGING_HTTP_PORT': '8083', 'OPENAI_API_KEY': 'ignored-ambient-key'}
        result = subprocess.run([sys.executable, '-c', program], env=baseline, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('isolated', result.stdout)
        for variation in ({'CHATBOT_PROVIDER': 'openai'}, {'CHATBOT_EVAL_ALLOW_LIVE': 'True'}):
            result = subprocess.run([sys.executable, '-c', program], env={**baseline, **variation}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cannot enable OpenAI', result.stderr)
