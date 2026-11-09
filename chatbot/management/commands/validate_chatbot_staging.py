"""Synthetic, rollback-only smoke suite; refuses ordinary or populated staging databases."""
from datetime import timedelta
from io import StringIO
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import Category
from faq.models import FAQ
from locations.models import RecyclingCenter
from chatbot.evaluation.dataset import DATA
from chatbot.evaluation.models import ChatEvaluationRun
from chatbot.evaluation.output import atomic_json
from chatbot.knowledge import KnowledgeService
from chatbot.models import ChatConversation, ChatMessage


class Command(BaseCommand):
    help = 'Validate only a NEW ephemeral staging database with synthetic fixtures, then roll back.'

    def handle(self, *args, **options):
        if os.getenv('TRUECA_STAGING_EPHEMERAL_VALIDATION') != '1':
            raise CommandError('Only an explicitly ephemeral validation environment is allowed.')
        call_command('check_chatbot_staging', ready=True, stdout=StringIO())
        if any(model.objects.exists() for model in (get_user_model(), FAQ, ChatConversation, ChatEvaluationRun)):
            raise CommandError('Refusing populated staging; use a new ephemeral validation database.')
        checks = []

        def require(condition, description):
            if not condition:
                raise CommandError('Synthetic staging smoke failed: ' + description)
            checks.append(description)

        with transaction.atomic(), patch('chatbot.providers.AsyncOpenAI', side_effect=AssertionError('SDK forbidden')):
            anon = Client(enforce_csrf_checks=True)
            session = anon.get('/chatbot/api/session/').json()
            conversation = session['conversation']['id']
            token = anon.cookies[settings.CSRF_COOKIE_NAME].value

            def send(client, conversation_id, content, request_id=None):
                return client.post('/chatbot/api/messages/', {
                    'conversation_id': conversation_id, 'request_id': str(request_id or uuid4()), 'content': content,
                }, content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value)

            missing_csrf = anon.post('/chatbot/api/messages/', {}, content_type='application/json')
            require(missing_csrf.status_code == 403, 'CSRF rejects missing token')
            origin = anon.post('/chatbot/api/reset/', {}, content_type='application/json',
                               HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN='https://outside.invalid')
            require(origin.status_code == 403, 'Origin rejects external host')
            for index in range(10):
                if index == 6:
                    rejected = send(anon, conversation, 'Cómo funciona trueca?')
                    require(rejected.status_code == 429 and rejected.json()['error'] == 'rate_limited', 'rate limit enforced')
                    # Advance only this synthetic fixture's request window; keep real limits unchanged.
                    ChatMessage.objects.filter(conversation_id=conversation).update(created_at=timezone.now()-timedelta(seconds=61))
                result = send(anon, conversation, 'Cómo funciona trueca?')
                require(result.status_code == 200 and result.json()['usage']['remaining'] == 9-index, f'anonymous quota {index+1}')
                require('intercambio sostenible' in result.json()['messages'][-1]['content'], 'deterministic description')
            require(send(anon, conversation, 'Otra pregunta').json()['error'] == 'quota_exhausted', 'question 11 blocked')
            history = anon.get('/chatbot/api/session/').json()
            require(len(history['messages']) == 20 and history['messages'][-1]['sources'][0]['available'], 'history and sources recovered')
            reset = anon.post('/chatbot/api/reset/', {}, content_type='application/json', HTTP_X_CSRFTOKEN=token)
            require(reset.status_code == 200 and reset.json()['usage']['remaining'] == 0, 'reset preserves quota')

            corpus = json.loads((DATA / 'corpus-v1.json').read_text())['faqs']
            for definition in corpus.values():
                FAQ.objects.create(**definition)
            Category.objects.create(name='Deporte sintético', slug='deporte-staging')
            center = RecyclingCenter.objects.create(name='Punto Limpio de Adeje', slug='adeje-staging',
                address='Dirección pública sintética', latitude='28.123456', longitude='-16.123456',
                schedule='Lunes 09:00–14:00', show_on_public_map=True)
            require(bool(KnowledgeService().retrieve('categorías').fragments), 'public categories')
            require(any(f.source.source_id == f'center:{center.pk}' for f in KnowledgeService().retrieve('centros horarios').fragments), 'public centers')
            user = get_user_model().objects.create_user(email='synthetic-staging@example.invalid')
            auth = Client(enforce_csrf_checks=True); auth.force_login(user)
            owned = auth.get('/chatbot/api/session/').json()['conversation']['id']
            require(auth.get('/chatbot/api/session/', {'conversation_id': conversation}).status_code == 404, 'conversation ownership')
            key = uuid4()
            for mode in ('extractive', 'grounded'):
                with override_settings(CHATBOT_GENERATION_MODE=mode):
                    result = send(auth, owned, '¿Cómo preparar fotografías para publicar?', request_id=key)
                    require(result.status_code == 200 and result.json()['usage']['remaining'] is None, f'{mode} mock and authenticated quota')
                    require(any(s['available'] for s in result.json()['messages'][-1]['sources']), f'{mode} references')
                duplicate = send(auth, owned, '¿Cómo preparar fotografías para publicar?', request_id=key)
                require(duplicate.status_code == 200, 'duplicate accepted without a new message')
                key = uuid4()
            require(ChatMessage.objects.filter(conversation_id=owned, role='user').count() == 2, 'duplicates persist once')
            private = FAQ.objects.filter(is_published=False).first()
            require(private is not None and private.answer not in str(result.json()), 'unpublished FAQ excluded')
            preview = StringIO()
            scope = {'case_ids': list(settings.CHATBOT_EVAL_CASE_IDS)} if settings.CHATBOT_EVAL_CASE_IDS else {}
            call_command('evaluate_chatbot_live', dry_run=True, stdout=preview, **scope)
            report = json.loads(preview.getvalue())
            require(report['real_openai_calls'] == 0 and not ChatEvaluationRun.objects.exists(), 'dry-run no SDK or ledger writes')
            atomic_json('/app/evaluation_reports/preview.json', report)
            FAQ.objects.filter(is_published=True).update(is_published=False)
            history = auth.get('/chatbot/api/session/').json()
            require(all(not s['available'] for m in history['messages'] for s in m.get('sources', [])), 'withdrawn references unavailable')
            transaction.set_rollback(True)
        require(not get_user_model().objects.exists() and not ChatConversation.objects.exists()
                and not FAQ.objects.exists() and not ChatEvaluationRun.objects.exists(), 'synthetic data rolled back')
        self.stdout.write(json.dumps({'checks': checks, 'passed': len(checks), 'real_openai_calls': 0,
                                      'data_rolled_back': True, 'preview': '/app/evaluation_reports/preview.json'}))
