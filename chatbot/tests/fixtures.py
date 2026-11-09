"""Synthetic public evidence for provider/transaction regression tests, not business rules."""
import json

from django.utils import timezone

from faq.models import FAQ
from chatbot.knowledge.contracts import KnowledgeBundle
from chatbot.knowledge.context import build_context, KnowledgeLimits
from chatbot.knowledge.sources.base import fragment


class ProviderEvidenceMixin:
    def setUp(self):
        super().setUp()
        self.provider_faq = FAQ.objects.create(
            question='Hola Hello Hi Pendiente Pregunta Question Last puntos recibo receive many Old Recent Current AUDIT_PRIVATE_QUESTION',
            answer='Answer',
        )


def knowledge_fixture(content='Answer'):
    now = timezone.now()
    item = fragment('faq', 1, 'Información pública', content, url='/faq/#faq-question-1',
                    updated_at=None, now=now, relevance=1)
    items, context, conflicts, excluded = build_context([item], limits=KnowledgeLimits(5, 1200, 6000), now=now)
    return KnowledgeBundle('es', items, (), conflicts, 'grounded', context, excluded)


def selection_json(source_id='faq:1', content='Answer', *, display_text=None):
    return json.dumps({'selections': [{'source_id': source_id, 'content': content,
                                     **({'display_text': display_text} if display_text is not None else {})}]}, ensure_ascii=False)
