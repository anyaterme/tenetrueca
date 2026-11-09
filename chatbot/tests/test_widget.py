import json
import re
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import translation

from chatbot.models import ChatConversation


@override_settings(CHATBOT_ENABLED=True)
class ChatbotWidgetTests(TestCase):
    def config(self, response):
        match = re.search(r'<script id="chatbot-config" type="application/json">(.*?)</script>',
                          response.content.decode(), re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_anonymous_portal_loads_one_widget_without_creating_conversation_in_template(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-chatbot aria-label=', count=1)
        self.assertContains(response, 'id="chatbot-panel"', count=1)
        self.assertContains(response, 'css/chatbot.css')
        self.assertContains(response, 'js/chatbot.js')
        self.assertContains(response, 'data-chatbot-suggestion', count=4)  # Container + three buttons.
        self.assertEqual(ChatConversation.objects.count(), 0)
        config = self.config(response)
        self.assertEqual((config['sessionUrl'], config['messagesUrl'], config['resetUrl']),
                         ('/chatbot/api/session/', '/chatbot/api/messages/', '/chatbot/api/reset/'))

    def test_authenticated_portal_uses_same_partial_and_keeps_mobile_navigation(self):
        user = get_user_model().objects.create_user(email='widget@example.test')
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertContains(response, 'data-chatbot aria-label=', count=1)
        self.assertContains(response, 'citizen-mobile-nav')
        self.assertContains(response, 'body--citizen-nav')
        self.assertNotIn(user.email, json.dumps(self.config(response)))

    @override_settings(CHATBOT_ENABLED=False)
    def test_feature_flag_removes_widget_and_its_assets(self):
        response = self.client.get('/')
        self.assertNotContains(response, 'data-chatbot')
        self.assertNotContains(response, 'chatbot-config')
        self.assertNotContains(response, 'js/chatbot.js')
        self.assertNotContains(response, 'css/chatbot.css')

    @override_settings(CHATBOT_MAX_INPUT_CHARS=75, CSRF_COOKIE_NAME='trueca_csrf')
    def test_presentation_configuration_respects_existing_settings(self):
        response = self.client.get('/')
        self.assertContains(response, 'maxlength="75"')
        config = self.config(response)
        self.assertEqual(config['maxInputChars'], 75)
        self.assertEqual(config['csrfCookieName'], 'trueca_csrf')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_english_uses_django_active_language_in_markup_and_javascript(self):
        with translation.override('en'):
            response = self.client.get('/')
        self.assertContains(response, '<html lang="en">')
        self.assertContains(response, 'Open the TRUEC@ assistant')
        self.assertContains(response, 'How does TRUEC@ work?')
        self.assertContains(response, 'How can I earn points?')
        self.assertContains(response, 'What items can I give?')
        self.assertContains(response, 'Close the assistant')
        self.assertEqual(self.config(response)['strings']['assistant'], 'TRUEC@ Assistant')
        self.assertIn('questions left', self.config(response)['strings']['remaining'])

    def test_spanish_uses_django_active_language(self):
        with translation.override('es'):
            response = self.client.get('/')
        self.assertContains(response, '<html lang="es">')
        self.assertContains(response, '¿Cómo puedo conseguir puntos?')
        self.assertEqual(self.config(response)['strings']['retry'], 'Reintentar')

    def test_conversation_content_is_fetched_from_api_not_embedded_in_html(self):
        conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']
        private_text = 'private-widget-history-' + uuid4().hex
        self.client.post('/chatbot/api/messages/', {'conversation_id': conversation,
                         'request_id': str(uuid4()), 'content': private_text}, content_type='application/json')
        response = self.client.get('/')
        self.assertNotContains(response, private_text)
        self.assertNotIn(conversation, json.dumps(self.config(response)))

    def test_dialog_and_log_have_accessible_names_and_login_routes_are_reused(self):
        response = self.client.get('/')
        self.assertContains(response, 'aria-labelledby="chatbot-title"')
        self.assertContains(response, 'aria-controls="chatbot-panel"')
        self.assertContains(response, 'role="log" aria-live="off"')
        self.assertContains(response, 'for="chatbot-input"')
        self.assertContains(response, 'href="/cuenta/login/"')
        self.assertContains(response, 'href="/cuenta/registro/"')


    @override_settings(CHATBOT_PROVIDER='openai')
    def test_live_widget_label_and_rate_error_are_localized_without_leaking_configuration(self):
        with translation.override('en'):
            response = self.client.get('/')
        self.assertContains(response, 'AI assistant')
        self.assertNotContains(response, '>Demo assistant<')
        config = self.config(response)
        self.assertFalse(config['isSimulated'])
        self.assertEqual(config['requestTimeoutMs'], 35000)
        self.assertIn('Wait a moment', config['strings']['rateLimited'])
        self.assertNotIn('OPENAI_API_KEY', str(config))
        self.assertNotIn('gpt-6-luna', str(config))


    @override_settings(OPENAI_API_KEY='AUDIT_SECRET_MARKER')
    def test_api_key_is_absent_from_all_rendered_markup_and_client_configuration(self):
        response = self.client.get('/')
        self.assertNotContains(response, 'AUDIT_SECRET_MARKER')
        self.assertNotContains(response, 'OPENAI_API_KEY')
        self.assertNotIn('AUDIT_SECRET_MARKER', json.dumps(self.config(response)))
