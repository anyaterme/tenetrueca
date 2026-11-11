from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from catalog.models import Category
from faq.models import FAQ


@override_settings(CHATBOT_ENABLED=True, CHATBOT_GENERATION_MODE='extractive')
class PublicationQuestionsTests(TestCase):
    def setUp(self):
        FAQ.objects.create(question='¿Qué es TRUEC@?', answer=(
            'TRUEC@ permite reservar objetos, añadir nuevos objetos y dejar los artículos que ya no usas.'))
        self.publish = FAQ.objects.create(question='¿Cómo añado un producto?', answer=(
            'Abre «Añadir producto», sube una foto con una descripción y envía tu propuesta.'))
        self.admitted = FAQ.objects.create(question='¿Qué productos puedo llevar al centro TRUEC@?', answer=(
            'Puedes llevar artículos deportivos y juguetes. El personal comprueba si el objeto es apto.'))
        self.excluded = FAQ.objects.create(question='¿Qué productos no puedo llevar al centro TRUEC@?', answer=(
            'No se aceptan ropa, calzado ni electrodomésticos grandes. Sin embargo, puedes entregar pequeños '
            'electrodomésticos, que no se incorporan al catálogo.'))
        self.conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']

    def ask(self, question):
        with translation.override('es'):
            response = self.client.post('/chatbot/api/messages/', {
                'conversation_id': self.conversation, 'request_id': str(uuid4()), 'content': question,
            }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        return response.json()['messages'][-1]

    def assert_faq(self, message, faq):
        self.assertEqual(message['content'], faq.answer)
        self.assertEqual([source['url'] for source in message['sources']],
                         [f"{reverse('faq:public_list')}#faq-question-{faq.pk}"])

    def test_article_wording_uses_publication_steps_without_the_stock_preamble(self):
        self.assert_faq(self.ask('como puedo publicar un artículo?'), self.publish)

    def test_admission_clarifies_then_looks_up_each_new_item_in_the_same_conversation(self):
        first = self.ask('Tengo un artículo para dejar')
        self.assertIn('¿Qué objeto quieres dejar?', first['content'])
        self.assertFalse(first['sources'])

        self.assert_faq(self.ask('Una bici'), self.admitted)
        self.assert_faq(self.ask('¿Y ropa?'), self.excluded)
        self.assert_faq(self.ask('¿Puedo dejar una cafetera pequeña?'), self.excluded)

        Category.objects.create(name='Proyector', slug='proyector')
        unknown = self.ask('¿Y un proyector?')
        self.assertIn('No tengo información suficiente', unknown['content'])
        self.assertFalse(unknown['sources'])
