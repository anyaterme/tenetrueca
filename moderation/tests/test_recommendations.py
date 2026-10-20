from unittest.mock import patch
from html.parser import HTMLParser
import json
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assistant.fixtures import simulated_response
from assistant.models import PublicationEvaluation
from assistant.rules import PROMPT_VERSION, RULES_VERSION
from assistant.services import validate_result
from assistant.snapshots import publication_snapshot
from catalog.models import Category
from core.roles import ROLE_STAFF_ADMIN
from moderation.forms import ModerationDecisionForm
from moderation.recommendations import decision_proposal
from publications.models import Publication


def checked_decisions(response):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.checked = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('name') == 'decision' and 'checked' in attrs:
                self.checked.append(attrs['value'])

    parser = Parser()
    parser.feed(response.content.decode())
    return parser.checked


class ModerationRecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.citizen = User.objects.create_user(username='proposal-citizen', email='citizen@example.test')
        cls.admin = User.objects.create_user(username='proposal-admin', email='admin@example.test')
        cls.admin.groups.add(Group.objects.get_or_create(name=ROLE_STAFF_ADMIN)[0])
        parent = Category.objects.create(name='Deporte', slug='proposal-deporte')
        cls.category = Category.objects.create(name='Pelotas', slug='proposal-pelotas', parent=parent)

    def setUp(self):
        self.publication = Publication.objects.create(
            submitter=self.citizen, title='Pelota', description='Pelota de 20 × 20 × 20 cm.',
            category=self.category, status='pending_review', submitted_at=timezone.now(),
        )
        self.client.force_login(self.admin)
        self.url = reverse('moderation:detail', args=[self.publication.pk])
        self.decide_url = reverse('moderation:decide', args=[self.publication.pk])

    def evaluation(self, scenario='suitable', *, payload=None, simulated=True):
        # Seed a saved evaluation with offline fixtures; no OpenAI client is constructed.
        fingerprint, snapshot = publication_snapshot(self.publication)
        evaluation = PublicationEvaluation.objects.create(
            publication=self.publication, fingerprint=fingerprint, snapshot=snapshot,
            model='fixture-model', rules_version=RULES_VERSION, prompt_version=PROMPT_VERSION,
            is_simulated=simulated,
        )
        evaluation.result = validate_result(payload or simulated_response(scenario), evaluation)
        evaluation.status = 'completed'
        evaluation.save(update_fields=['result', 'status'])
        return evaluation

    def test_decision_card_precedes_evaluation_and_no_draft_without_evaluation(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertLess(html.index('id="decision-title"'), html.index('id="ai-evaluation-title"'))
        self.assertIsNone(response.context['ai_decision_proposal'])
        self.assertIsNone(response.context['decision_form']['decision'].value())
        self.assertIsNone(response.context['decision_form']['notes'].value())

    def test_approval_draft_only_for_fully_suitable_result(self):
        evaluation = self.evaluation()
        proposal = decision_proposal(evaluation)
        self.assertEqual(proposal['decision'], 'approve')
        response = self.client.get(self.url)
        self.assertEqual(response.context['decision_form']['decision'].value(), 'approve')
        self.assertContains(response, 'Recomendación IA: Aprobar')
        self.assertContains(response, '(simulada)')
        self.assertEqual(checked_decisions(response), ['approve'])

    def test_ineligible_item_proposes_reject_with_explanation(self):
        evaluation = self.evaluation('small_appliance')
        proposal = decision_proposal(evaluation)
        self.assertEqual(proposal['decision'], 'reject')
        self.assertIn('no publicarse', proposal['reason'])
        self.assertIn('entrega', proposal['indications'])
        response = self.client.get(self.url)
        self.assertEqual(response.context['decision_form']['decision'].value(), 'reject')
        self.assertContains(response, 'Recomendación IA: Rechazar')
        self.assertEqual(checked_decisions(response), ['reject'])

    def test_correctable_issues_prefill_changes_reason_and_instructions(self):
        payload = simulated_response('suitable')
        payload['checks']['description'] = {
            'status': 'INCIDENCIA', 'explanation': 'Falta el estado real y la lista de piezas.',
        }
        payload['checks']['photo_quality'] = {
            'status': 'INCIDENCIA', 'explanation': 'La foto de presentación no muestra el estado.',
        }
        payload['recommendations'] = ['Añade fotos de frente y espalda y confirma las piezas incluidas.']
        evaluation = self.evaluation(payload=payload, simulated=False)
        with patch('assistant.providers.OpenAIProvider.evaluate') as evaluate:
            response = self.client.get(self.url)
            evaluate.assert_not_called()
        form = response.context['decision_form']
        proposal = response.context['ai_decision_proposal']
        self.assertEqual(form['decision'].value(), 'request_changes')
        self.assertEqual(form['notes'].value(), proposal['notes'])
        self.assertIn('Motivo:', form['notes'].value())
        self.assertIn('Indicaciones:', form['notes'].value())
        self.assertIn('estado real', form['notes'].value())
        self.assertIn('frente y espalda', form['notes'].value())
        self.assertEqual(proposal['evaluation_id'], evaluation.pk)
        self.assertContains(response, 'Recomendación IA: Solicitar cambios')
        self.assertEqual(checked_decisions(response), ['request_changes'])
        self.assertNotContains(response, '(simulada)')
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, 'pending_review')
        self.assertFalse(self.publication.moderation_decisions.exists())

    def test_uncertainty_or_physical_check_never_proposes_approval(self):
        for scenario in ('unverifiable', 'visual'):
            with self.subTest(scenario=scenario):
                payload = simulated_response(scenario)
                payload['recommendations'] = []  # Server supplies actionable fallbacks.
                evaluation = self.evaluation(payload=payload)
                proposal = decision_proposal(evaluation)
                self.assertEqual(proposal['decision'], 'request_changes')
                self.assertIn('físicamente', proposal['indications'])
                self.assertIn('Pendiente de verificar:', proposal['reason'])

    def test_admission_incident_without_prohibited_item_requests_clarification(self):
        payload = simulated_response('suitable')
        payload['checks']['admission'] = {'status': 'INCIDENCIA', 'explanation': 'Faltan datos para admitirlo.'}
        evaluation = self.evaluation(payload=payload)
        self.assertEqual(decision_proposal(evaluation)['decision'], 'request_changes')

    def test_error_pending_processing_and_obsolete_evaluations_do_not_prefill(self):
        evaluation = self.evaluation()
        for status in ('error', 'pending', 'processing', 'obsolete'):
            with self.subTest(status=status):
                evaluation.status = status
                evaluation.save(update_fields=['status'])
                response = self.client.get(self.url)
                self.assertIsNone(response.context['ai_decision_proposal'])
                self.assertIsNone(response.context['decision_form']['decision'].value())

    def test_stale_completed_evaluation_does_not_prefill(self):
        self.evaluation()
        self.publication.description = 'Descripción corregida.'
        self.publication.save(update_fields=['description'])
        response = self.client.get(self.url)
        self.assertIsNone(response.context['ai_decision_proposal'])
        self.assertIsNone(response.context['decision_form']['notes'].value())

    def test_manual_decision_can_override_proposal_and_keeps_user_notes(self):
        evaluation = self.evaluation('prohibited')
        self.assertEqual(decision_proposal(evaluation)['decision'], 'reject')
        response = self.client.post(self.decide_url, {'decision': 'approve', 'notes': 'Revisión humana confirmada.'})
        self.assertEqual(response.status_code, 302)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, 'approved')
        decision = self.publication.moderation_decisions.get()
        self.assertEqual(decision.decision, 'approve')
        self.assertEqual(decision.notes, 'Revisión humana confirmada.')
        self.assertEqual(decision.ai_evaluation_id, evaluation.pk)
        response = self.client.get(self.url)
        self.assertIsNone(response.context['ai_decision_proposal'])
        self.assertNotContains(response, 'Recomendación IA:')

    def test_invalid_post_preserves_manual_selection_and_does_not_reapply_draft(self):
        self.evaluation('unverifiable')
        response = self.client.post(self.decide_url, {'decision': 'reject', 'notes': ''})
        self.assertEqual(response.status_code, 200)
        form = response.context['decision_form']
        self.assertTrue(form.is_bound)
        self.assertEqual(form['decision'].value(), 'reject')
        self.assertEqual(form['notes'].value(), '')
        self.assertEqual(checked_decisions(response), ['reject'])
        self.assertIn('notes', form.errors)
        response = self.client.post(self.decide_url, {'notes': 'Texto del gestor sin decisión.'})
        form = response.context['decision_form']
        self.assertIsNone(form['decision'].value())
        self.assertEqual(form['notes'].value(), 'Texto del gestor sin decisión.')
        self.assertIn('decision', form.errors)
        self.assertEqual(checked_decisions(response), [])
        self.assertFalse(self.publication.moderation_decisions.exists())

    def test_proposed_notes_are_bounded_and_html_escaped(self):
        payload = simulated_response('suitable')
        payload['checks']['description'] = {
            'status': 'INCIDENCIA', 'explanation': '</textarea><script>alert(1)</script>' + ' texto' * 150,
        }
        payload['recommendations'] = ['Revisar ' + 'detalle ' * 180] * 4
        evaluation = self.evaluation(payload=payload)
        proposal = decision_proposal(evaluation)
        self.assertLessEqual(len(proposal['notes']), 2000)
        self.assertTrue(ModerationDecisionForm(data=proposal).is_valid())
        response = self.client.get(self.url)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_each_choice_has_its_own_safe_draft_without_fabricating_findings(self):
        for scenario in ('suitable', 'unverifiable', 'prohibited'):
            with self.subTest(scenario=scenario):
                evaluation = self.evaluation(scenario)
                proposal = decision_proposal(evaluation)
                drafts = proposal['drafts']
                self.assertEqual(set(drafts), {'approve', 'reject', 'request_changes'})
                self.assertEqual(len(set(drafts.values())), 3)
                self.assertEqual(drafts[proposal['decision']], proposal['notes'])
                for decision, notes in drafts.items():
                    self.assertTrue(ModerationDecisionForm(data={'decision': decision, 'notes': notes}).is_valid())
                    self.assertLessEqual(len(notes), 2000)
                if proposal['decision'] != 'approve':
                    self.assertIn('sujeta a revisión humana', drafts['approve'])
                    self.assertNotIn('no detecta incidencias', drafts['approve'])
                if scenario == 'suitable':
                    self.assertIn('concreta aquí el motivo', drafts['reject'])
                    self.assertIn('qué cambios necesitas', drafts['request_changes'])
                    self.assertNotIn('no está admitido', drafts['reject'])

    def test_choice_drafts_are_json_escaped_and_bound_manual_notes_stay_intact(self):
        payload = simulated_response('suitable')
        payload['checks']['description'] = {
            'status': 'INCIDENCIA', 'explanation': '</script><script>alert(1)</script>',
        }
        evaluation = self.evaluation(payload=payload)
        with patch('assistant.providers.OpenAIProvider.evaluate') as evaluate:
            response = self.client.post(self.decide_url, {'notes': 'Mi motivo y mis indicaciones.'})
            evaluate.assert_not_called()
        html = response.content.decode()
        source = re.search(r'<script id="moderation-decision-drafts" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(source)
        self.assertEqual(json.loads(source.group(1)), decision_proposal(evaluation)['drafts'])
        self.assertNotIn('<script>', source.group(1))
        self.assertEqual(response.context['decision_form']['notes'].value(), 'Mi motivo y mis indicaciones.')
        self.assertContains(response, 'data-decision-drafts="moderation-decision-drafts"')
        self.assertContains(response, 'js/moderation_decision.js')
        self.assertFalse(self.publication.moderation_decisions.exists())
