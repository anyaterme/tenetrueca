"""Public chat regressions for the natural answer mode, with no live API calls."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from catalog.models import Category
from faq.models import FAQ

from chatbot.grounding import EvidenceSelection, GeneratedAnswer, GroundedClaim
from chatbot.tests.test_openai_provider import response


PUBLISHED = {
    'overview': ('¿Qué es TRUEC@?', 'TRUEC@ permite ver el catálogo y añadir nuevos objetos.'),
    'publication': ('¿Cómo añado un producto?',
                    '1. Ir a añadir productos.\r\n'
                    '2. Sube una foto y descripción del objeto que quieras depositar y publícalo.\r\n'
                    '3. Lleva el objeto a uno de los 8 Centros TRUEC@ de los puntos limpios fijos de la isla de Tenerife.\r\n'
                    '4. Identifícate con tu DNI/NIE al gestor del punto limpio que validará el objeto y quedará disponible para otro usuario.'),
    'admission': ('¿Qué productos puedo llevar al centro TRUEC@?',
                  'Puedes llevar artículos deportivos, juguetes y accesorios infantiles, artículos para el hogar, entre otros. '
                  'Consulta las categorías para tener una idea de los productos que se aceptan. '
                  'Los operarios de los puntos limpios te indicarán si un proudcto es apto para la red de intercambio sostenible.'),
    'exclusions': ('¿Qué productos NO puedo llevar al centro TRUEC@?',
                   'No se aceptarán ropa, calzado, objetos eléctricos RAEEs, electrodomésticos grandes, alimentos, bebidas ni plantas. '
                   'Sin embargo, sí podrás obtener puntos por la entrega de pequeños electrodomésticos, que serán gestionados '
                   'por personal autorizado aunque no entrarán a formar parte del catálogo. También están prohibidos los objetos '
                   'superiores a 50cmx50cmx50cm, por limitaciones en el espacio disponible para transporte y almacenamiento.'),
    'reservation': ('¿Cómo reservo un objeto?', '1. Ir a Catálogo de objetos.\n2. Reserva el objeto deseado.\n'
                    '3. Acude al punto limpio donde se encuentre el objeto reservado en un plazo de 7 días.'),
    'deadline': ('¿cuánto tiempo tengo para recoger un objeto reservado?',
                 'Dispones de 7 días naturales desde la reserva para recoger el objeto en el centro o almacén TRUEC@ correspondiente.'),
    'missed': ('¿Qué sucede si no recojo el objeto reservado?', 'Si no recoges el objeto ni cancelas la reserva dentro del plazo '
               'establecido, serás penalizado con 7 días sin poder realizar nuevas reservas, y el objeto quedará disponible para otros usuarios.'),
}
GUIDE = ('La sección de ayuda reúne guías prácticas sobre el intercambio sostenible. '
         'Allí encontrarás orientaciones para preparar una propuesta.')


@override_settings(CHATBOT_ENABLED=True, CHATBOT_GENERATION_MODE='natural')
class NaturalModeAPITests(TestCase):
    def setUp(self):
        self.conversation = self.client.get('/chatbot/api/session/').json()['conversation']['id']

    def ask(self, question, language='es'):
        with translation.override(language):
            result = self.client.post('/chatbot/api/messages/', {
                'conversation_id': self.conversation, 'request_id': str(uuid4()), 'content': question,
            }, content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['messages'][-1]

    def faq(self, key):
        title, text = PUBLISHED[key]
        return FAQ.objects.create(question=title, answer=text)

    def assert_source(self, message, faq):
        self.assertEqual(message['sources'], [{
            'available': True, 'type': 'faq', 'title': faq.question,
            'url': f"{reverse('faq:public_list')}#faq-question-{faq.pk}",
        }])
        self.assertNotIn(faq.answer, message['content'])
        self.assertNotIn('en su idioma original', message['content'])

    def test_replays_public_audit_and_answers_each_item_from_the_right_source(self):
        self.faq('overview')
        publication, admission, exclusions = (self.faq(key) for key in ('publication', 'admission', 'exclusions'))
        Category.objects.create(name='Proyectores', slug='proyectores')
        with patch('chatbot.services.get_provider') as provider:
            first = self.ask('¿Cómo puedo publicar un artículo?')
            self.assertIn('foto y una descripción', first['content'])
            self.assert_source(first, publication)
            self.assertIn('¿Qué objeto quieres dejar?', self.ask('Tengo un artículo para dejar')['content'])

            bicycle = self.ask('¿Y una bicicleta?')
            self.assertIn('material deportivo', bicycle['content'])
            self.assertIn('confirma si es apta', bicycle['content'])
            self.assert_source(bicycle, admission)

            coffee_maker = self.ask('¿Y una cafetera pequeña?')
            self.assertIn('obtener puntos', coffee_maker['content'])
            self.assertIn('fuera del catálogo', coffee_maker['content'])
            self.assertIn('50 × 50 × 50 cm', coffee_maker['content'])
            self.assert_source(coffee_maker, exclusions)

            clothes = self.ask('¿Y ropa?')
            self.assertIn('La ropa queda fuera', clothes['content'])
            self.assertNotIn('electrodoméstico', clothes['content'])
            self.assert_source(clothes, exclusions)
            self.assertEqual(self.ask('¿Puedo llevarla?')['content'], clothes['content'])

            repeated = self.ask('¿Cómo añado un producto?')
            self.assertEqual(first['content'], repeated['content'])
            delivery = self.ask('¿Y después?')
            self.assertIn('muestra tu DNI o NIE', delivery['content'])
            self.assert_source(delivery, publication)
            self.assertFalse(self.ask('¿Y un proyector?')['sources'])
            provider.assert_not_called()

    def test_ambiguous_new_object_with_only_a_catalogue_category_is_not_approved(self):
        self.faq('admission')
        Category.objects.create(name='Proyector', slug='proyector')
        self.ask('Quiero dejar un artículo')
        unknown = self.ask('¿Y un proyector?')
        self.assertIn('No tengo información suficiente', unknown['content'])
        self.assertFalse(unknown['sources'])

    def test_colloquial_publication_questions_find_the_same_official_procedure(self):
        publication = self.faq('publication')
        expected = self.ask('¿Cómo puedo publicar un artículo?')['content']
        for question in (
            'qué tengo que hacer para poder subir un artículo?',
            '¿Qué necesito para añadir un producto?',
            '¿Cuáles son los pasos para publicar un objeto?',
        ):
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual(answer['content'], expected)
                self.assert_source(answer, publication)
        english = self.ask('What do I need to do to submit an item?')
        self.assertIn('photo and description', english['content'])
        self.assert_source(english, publication)
        self.assertFalse(self.ask('¿Qué tengo que hacer para subir de nivel?')['sources'])

    def test_followup_can_refer_to_an_earlier_question_in_the_same_message(self):
        exclusions = self.faq('exclusions')
        answer = self.ask('¿Puedo llevar ropa? ¿Y una cafetera pequeña?')
        self.assertIn('La ropa queda fuera', answer['content'])
        self.assertIn('Si es un electrodoméstico pequeño', answer['content'])
        self.assert_source(answer, exclusions)

    def test_reservation_followups_preserve_deadline_and_cancel_exception(self):
        reservation, deadline, missed = (self.faq(key) for key in ('reservation', 'deadline', 'missed'))
        first = self.ask('¿Cómo reservo un objeto?')
        self.assertIn('Elige el artículo en el catálogo', first['content'])
        self.assert_source(first, reservation)
        timing = self.ask('¿Y cuánto tiempo tengo?')
        self.assertIn('7 días naturales a partir de la reserva', timing['content'])
        self.assert_source(timing, deadline)
        penalty = self.ask('¿Y si no lo recojo?')
        self.assertIn('o cancelado la reserva', penalty['content'])
        self.assertIn('7 días', penalty['content'])
        self.assert_source(penalty, missed)

    def test_an_edited_or_withdrawn_rule_does_not_reuse_the_reviewed_answer(self):
        deadline = self.faq('deadline')
        self.assert_source(self.ask(deadline.question), deadline)
        deadline.answer = 'Tienes 3 días naturales desde la reserva. Las reservas suspendidas tienen otro plazo.'
        deadline.save()
        changed = self.ask(deadline.question)
        self.assertNotIn('7 días', changed['content'])
        self.assert_source(changed, deadline)
        deadline.is_published = False
        deadline.save()
        self.assertFalse(self.ask(deadline.question)['sources'])
        old = self.client.get('/chatbot/api/session/').json()['messages'][1]
        self.assertEqual(old['sources'], [{'available': False}])

    @override_settings(CHATBOT_PROVIDER='openai', OPENAI_API_KEY='offline-test-key')
    def test_sdk_path_rephrases_new_faq_and_rejects_literal_or_unsupported_output(self):
        faq = FAQ.objects.create(question='¿Dónde están las guías de ayuda?', answer=GUIDE)

        def payload(text):
            return response(json.dumps({
                'answer': text, 'sufficient': True, 'claims': [{
                    'text': text, 'evidence': [{'source_id': f'faq:{faq.pk}', 'quote': GUIDE}],
                }],
            }))

        rephrased = 'En la ayuda encontrarás guías para preparar tu propuesta de intercambio.'
        copied = 'Puedes consultar la ayuda. ' + GUIDE
        invented = rephrased + ' La propuesta cuesta 50 puntos.'
        sdk = MagicMock()
        sdk.responses.create = AsyncMock(side_effect=[payload(rephrased), payload(copied), payload(invented)])
        with patch('chatbot.safety.is_test_process', return_value=False), patch('chatbot.providers.AsyncOpenAI') as factory:
            factory.return_value.__aenter__.return_value = sdk
            answer = self.ask(faq.question)
            self.assertEqual(answer['content'], rephrased)
            self.assert_source(answer, faq)
            for unsupported in (copied, invented):
                rejected = self.ask(faq.question)
                self.assertIn('No dispongo de información', rejected['content'])
                self.assertNotIn(unsupported, rejected['content'])
                self.assertFalse(rejected['sources'])
        self.assertEqual(sdk.responses.create.await_count, 3)

    def test_english_followup_can_use_the_same_reviewed_spanish_source(self):
        exclusions = self.faq('exclusions')
        self.ask('I have an item to drop off', language='en')
        answer = self.ask('And clothes?', language='en')
        self.assertEqual(answer['content'], 'TRUEC@ does not accept clothing.')
        self.assert_source(answer, exclusions)

    def test_switches_language_per_message_while_keeping_publication_context(self):
        publication = self.faq('publication')
        opening = self.ask('How do I submit an item?', language='es')
        self.assertIn('photo and description', opening['content'])
        self.assert_source(opening, publication)

        spanish = self.ask('¿Y las fotos?', language='en')
        self.assertIn('una foto y una descripción', spanish['content'])
        self.assert_source(spanish, publication)

        english = self.ask('And the photos?', language='es')
        self.assertIn('include a photo and description', english['content'])
        self.assert_source(english, publication)

    def test_ambiguous_turn_uses_last_clear_language_then_interface(self):
        initial = self.ask('iPhone?', language='es')
        self.assertIn('No dispongo de información', initial['content'])

        clarification = self.ask('I have an item to drop off', language='es')
        self.assertIn('What item would you like to drop off?', clarification['content'])

        followup = self.ask('iPhone?', language='es')
        self.assertIn('I do not have enough', followup['content'])

    def test_other_language_extract_is_replaced_by_a_localized_source_link(self):
        faq = FAQ.objects.create(question='¿Qué guías ofrece TRUEC@?', answer='You can read guides about the exchange.')
        with override_settings(CHATBOT_GENERATION_MODE='extractive'):
            answer = self.ask(faq.question, language='en')
        self.assertEqual(answer['content'], 'Puedes consultar la información publicada en la fuente enlazada.')
        self.assert_source(answer, faq)

    def test_english_photo_followup_reaches_the_provider_as_a_contextual_question_in_english(self):
        faq = FAQ.objects.create(question='¿Cómo preparar fotografías para publicar?',
                                 answer='Prepara fotografías claras del objeto para presentar la propuesta.')
        evidence = (EvidenceSelection(f'faq:{faq.pk}', faq.answer),)
        text = 'Use clear photographs of the object for your proposal.'
        with patch('chatbot.services.get_provider') as factory:
            provider = factory.return_value
            provider.respond.return_value = GeneratedAnswer(evidence, text, (GroundedClaim(text, evidence),))
            self.ask('¿Cómo puedo publicar un artículo?')
            answer = self.ask('And the photos?')
        history = provider.respond.call_args.args[0]
        self.assertEqual(history[-1].content, 'What photos should I prepare to publish an item?')
        self.assertEqual(provider.respond.call_args.kwargs['language'], 'en')
        self.assertEqual(answer['content'], text)
        self.assert_source(answer, faq)
