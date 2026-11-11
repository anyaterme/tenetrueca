"""Conversation routing, local answers and freshly validated grounded generation."""
import json
import logging
import re
from hashlib import sha256
from dataclasses import replace

from django.conf import settings
from django.utils import timezone

from .conversation_context import ConversationContextResolver, split_questions
from .grounding import AssistantReply, EvidenceSelection, GeneratedAnswer, ValidatedReference
from .knowledge import KnowledgeService
from .knowledge.conflicts import conflicting_claims, unsafe_content
from .knowledge.context import KnowledgeLimits, build_context
from .knowledge.contracts import KnowledgeBundle
from .knowledge.normalization import fold, is_platform_overview, terms_for, words
from .knowledge.registry import official_faq
from .knowledge.reviewed_faq import definition, reviewed_answer, reviewed_intent
from .language import detect_language, message_language
from .providers import ChatTurn
from .safety import ProviderError
from .generation import critical, choose_mode, validate_grounded, validate_no_faq_copy, without_response_style
from .references import valid_public_url
from .response_style import needs_display_text, requested_sentences, sentence_count, sentences_in, validate_display_text, validate_style, with_response_style
from .sentence_planning import plan_three_sentences

logger = logging.getLogger(__name__)


TEXT = {
    'no_evidence': ('No dispongo de información pública suficiente para responder a esa pregunta.',
                    'I do not have enough public information to answer that question.'),
    'topic': ('¿Sobre qué aspecto de TRUEC@ quieres consultar?', 'Which aspect of TRUEC@ would you like to ask about?'),
    'item': ('¿Qué objeto quieres dejar? Así puedo orientarte sobre si se admite.',
             'What item would you like to drop off? Tell me what it is so I can check whether the FAQ covers it.'),
    'admission_unknown': ('No tengo información suficiente para confirmar si ese objeto se puede entregar. Consulta al equipo de TRUEC@ antes de llevarlo.',
                          'I do not have enough information to confirm whether that item can be dropped off. Check with TRUEC@ staff before taking it in.'),
    'center': ('¿De qué punto limpio quieres consultar la información?', 'Which recycling center would you like information about?'),
    'conflict': ('No dispongo de una regla confirmada para esa afirmación. Consulta al personal de TRUEC@.',
                 'I do not have a confirmed rule for that statement. Please ask TRUEC@ staff.'),
    'category': ('La existencia de una categoría no garantiza la admisión de un objeto.',
                 'The existence of a category does not guarantee an object will be accepted.'),
    'privacy': ('No puedo consultar datos privados de tu cuenta.', 'I cannot access private information from your account.'),
    'source_help': ('No puedo confirmar la respuesta con suficiente precisión. Puedes consultar la FAQ enlazada.',
                    'I cannot confirm the answer precisely enough. You can check the linked FAQ.'),
    'source_language': ('Puedes consultar la información publicada en la fuente enlazada.',
                        'You can check the published information at the linked source.'),
}


def local_text(code, language):
    return TEXT[code][int(str(language).lower().startswith('en'))]


def private_question(question):
    normalized = fold(question)
    return bool(re.search(r'\b(mi direccion(?: de entrega)?|my (?:delivery |home )?address)\b', normalized)
                or re.search(r'\b(consulta\w*|muestra\w*|show|check|access)\b.{0,45}\b(mi|mis|my)\b.{0,30}\b(reserv\w*|saldo|balance|qr)\b', normalized))


def restrict(bundle, resolved):
    fragments = bundle.fragments
    published = tuple(f for f in fragments if official_faq(f))
    if published:
        # Static intent IDs cannot erase an official FAQ or override its facts.
        fragments = published
    elif resolved.center_id:
        fragments = tuple(f for f in fragments if f.source.source_id == resolved.center_id)
    elif resolved.intent == 'platform.overview':
        # Prefer current public explanatory FAQ; a missing/ineligible FAQ must not
        # erase the platform's separately verified basic description.
        public_faq = tuple(f for f in fragments if f.source.source_type == 'faq')
        fragments = public_faq or tuple(f for f in fragments if 'platform.overview' in f.claim_ids)
    elif resolved.intent:
        fragments = tuple(f for f in fragments if resolved.intent in f.claim_ids)
    elif resolved.topic in ('publication', 'admission'):
        # The existence of a category or a general rule about points doesn't
        # answer whether a particular object can be brought in.
        fragments = ()
    elif resolved.topic == 'category':
        fragments = tuple(f for f in fragments if f.source.source_type in ('faq', 'category'))
    elif resolved.topic in ('points', 'moderation', 'reservation'):
        fragments = tuple(f for f in fragments if f.source.source_type == 'faq')
    else:
        # A partial word match on points is not sufficient to answer a complex question
        # with an unrelated registration/award rule.
        fragments = tuple(f for f in fragments if f.source.source_type != 'business_rule')
    if not resolved.intent:
        requested = set(terms_for(without_response_style(resolved.search_query)))
        focus = requested.intersection({'cancelar', 'diario', 'limite', 'saldo', 'qr', 'propio'})
        numbers = {term for term in requested if term.isdigit()}
        fragments = tuple(f for f in fragments if f.source.source_type != 'faq' or
                          (focus.issubset(set(terms_for(f.title + ' ' + f.content)))
                           and numbers.issubset(set(terms_for(f.content)))))
    selected, context, conflicts, exclusions = build_context(fragments, limits=KnowledgeLimits.configured(), now=timezone.now())
    ids = {f.source.source_id for f in selected}
    return replace(bundle, fragments=selected, context=context,
                   rules=tuple(r for r in bundle.rules if set(r.source_ids).issubset(ids)),
                   conflict_ids=tuple(sorted(set(bundle.conflict_ids).union(conflicts))),
                   exclusions=tuple(sorted(set(bundle.exclusions).union(exclusions))))


class ConversationAnswerer:
    def __init__(self, knowledge=None):
        self.knowledge = knowledge or KnowledgeService()
        self.resolver = ConversationContextResolver()

    def answer_message(self, question, recent_users, *, interface_language, provider_factory, history=()):
        if settings.CHATBOT_GENERATION_MODE == 'full_faq':
            from .full_faq_answering import answer_full
            return answer_full(question, history or tuple(ChatTurn('user', text) for text in recent_users),
                               interface_language=interface_language, provider_factory=provider_factory)
        language = message_language(question, recent_users, interface_language)
        reply = self.answer(question, recent_users, language=language, provider_factory=provider_factory)
        # A legacy extract or a center's free-form details can still be in the
        # source language. Keep the link and use a localized response instead.
        if any(detect_language(sentence) not in (None, language) for sentence in sentences_in(reply.text)):
            code = 'source_language' if any(ref.public_url for ref in reply.references) else 'no_evidence'
            return replace(reply, text=local_text(code, language), route='no_evidence', outcome='no_evidence')
        return reply

    def answer(self, question, recent_users=(), *, language, provider_factory):
        if settings.CHATBOT_GENERATION_MODE == 'full_faq':
            return self.answer_message(question, recent_users, interface_language=language,
                                       provider_factory=provider_factory)
        parts = split_questions(question)
        if not parts or unsafe_content(question):
            return AssistantReply(local_text('topic', language), route='clarification', outcome='clarification')
        if private_question(question):
            return AssistantReply(local_text('no_evidence', language) + '\n' + local_text('privacy', language),
                                  route='no_evidence', outcome='no_evidence')
        replies, records, pending, routes = [], [], [], []
        locally_formatted = False
        check_presentation = False
        natural = settings.CHATBOT_GENERATION_MODE == 'natural'
        preferred_titles = {}

        def retrieve(resolved, preferred_title):
            if preferred_title:
                bundle = self.knowledge.retrieve(resolved.search_query, language=language, preferred_title=preferred_title)
            else:
                bundle = self.knowledge.retrieve(resolved.search_query, language=language)
            return restrict(bundle, resolved)

        for index, part in enumerate(parts):
            context_users = (*recent_users, *parts[:index]) if natural else recent_users
            resolved = self.resolver.resolve(part, context_users, natural=natural, language=language)
            if resolved.clarification:
                replies.append(local_text(resolved.clarification, language))
                routes.append('clarification')
                continue
            key = reviewed_intent(resolved.search_query, resolved.topic, intent=resolved.intent,
                                  original=resolved.original) if natural else ''
            approved = definition(key)
            preferred_title = approved.title if approved else ''
            preferred_titles[resolved] = preferred_title
            bundle = retrieve(resolved, preferred_title)
            if conflicting_claims(resolved.search_query) and not any(official_faq(f) for f in bundle.fragments):
                replies.append(local_text('conflict', language))
                routes.append('conflict')
                continue
            if resolved.intent:
                fact = next((r for r in bundle.rules if r.claim_id == resolved.intent and r.validation == 'verified'), None)
                if fact is not None:
                    text = dict(fact.localized_content).get('en' if language.startswith('en') else 'es')
                    if text and (not natural or all(f.source.public_url for f in bundle.fragments if f.source.source_id in fact.source_ids)):
                        replies.append(text)
                        used = tuple(f for f in bundle.fragments if f.source.source_id in fact.source_ids)
                        records.append((resolved, used, text))
                        routes.append('deterministic')
                        continue
            if not bundle.fragments:
                # Conflicts on discarded documents do not describe the user's claim.
                # Direct blocked questions were handled above; rule drift still fails closed.
                code = 'conflict' if (bundle.conflict_ids and resolved.topic != 'points' or
                                      any(c.startswith('approved_rule_drift:') for c in bundle.conflict_ids)) else 'no_evidence'
                replies.append(local_text('admission_unknown' if code == 'no_evidence' and
                                          resolved.topic == 'admission' else code, language))
                routes.append(code)
                continue
            if resolved.center_id and bundle.fragments[0].source.source_type == 'center':
                item = bundle.fragments[0]
                data = json.loads(item.content.partition('\n')[0])
                wanted = set(terms_for(part))
                if wanted.intersection({'hoy', 'ahora', 'festivo', 'festivos', 'today', 'now', 'currently', 'holiday', 'holidays'}):
                    replies.append(local_text('no_evidence', language))
                    routes.append('no_evidence')
                    continue
                fields = [key for term, key in (('horario', 'schedule'), ('telefono', 'phone'), ('direccion', 'address')) if term in wanted]
                if not fields and any(w in fold(part) for w in ('donde', 'where', 'ubicacion', 'location')):
                    fields = ['address']
                if fields and all(data.get(field) for field in fields):
                    text = data['name'] + ': ' + '\n'.join(data[field] for field in fields)
                    replies.append(text)
                    records.append((resolved, (item,), text))
                    routes.append('deterministic')
                else:
                    replies.append(local_text('no_evidence', language))
                    routes.append('no_evidence')
                continue
            if natural:
                reviewed = reviewed_answer(key, bundle.fragments, language)
                sentences = requested_sentences(question)
                if reviewed and (not sentences or len(parts) == 1 and sentence_count(reviewed[0]) == sentences):
                    text, source = reviewed
                    replies.append(text)
                    records.append((resolved, (source,), source.content))
                    routes.append('deterministic')
                    continue
                direct = tuple(f for f in bundle.fragments if official_faq(f) and (
                    (preferred_title and fold(f.title) == fold(preferred_title))
                    or words(f.title) == words(resolved.original)
                ))
                candidates = direct or bundle.fragments
                usable = tuple(f for f in candidates if official_faq(f)
                               and (resolved.topic == 'platform' or not is_platform_overview(f.title))
                               and not critical(f.title + '\n' + f.content))
                if not usable or critical(without_response_style(resolved.search_query)):
                    if direct:
                        replies.append(local_text('source_help', language))
                        records.extend((resolved, (source,), source.content) for source in direct[:2])
                    else:
                        replies.append(local_text('admission_unknown' if resolved.topic == 'admission' else 'no_evidence', language))
                    routes.append('no_evidence')
                    continue
                bundle = replace(bundle, fragments=usable)
            pending.append((resolved, bundle, len(replies)))
            replies.append('')
        if pending:
            fragments, context, conflicts, excluded = build_context(
                [f for _, b, _ in pending for f in b.fragments], limits=KnowledgeLimits.configured(), now=timezone.now())
            knowledge = KnowledgeBundle(language, fragments, (), conflicts, 'grounded', context, excluded)
            knowledge = replace(knowledge, generation_mode='grounded' if natural else choose_mode(question, knowledge))
            knowledge = with_response_style(knowledge, question, language)
            try:
                generated = None if natural else plan_three_sentences(question, knowledge)
                locally_formatted = generated is not None
                if generated is None:
                    prompt = question
                    if natural:
                        # Give the provider the resolved question, never raw chat history.
                        prompt = '\n'.join(r.provider_question or r.search_query for r, _, _ in pending)
                    elif len(parts) == 1 and pending[0][0].topic == 'admission':
                        resolved = pending[0][0]
                        if resolved.search_query != resolved.original:
                            prompt = resolved.search_query
                    generated = provider_factory().respond([ChatTurn('user', prompt)], language=language, knowledge=knowledge)
                if knowledge.generation_mode == 'grounded':
                    generated = validate_grounded(generated, knowledge)
                    if natural and generated.sufficient:
                        validate_no_faq_copy(generated.text, fragments)
                elif isinstance(generated, GeneratedAnswer) and (generated.text or generated.claims):
                    raise ProviderError('invalid_grounding')
                elif isinstance(generated, str):
                    matches = [f for f in fragments if f.content == generated]
                    if len(matches) != 1:
                        raise ProviderError('invalid_grounding')
                    generated = GeneratedAnswer((EvidenceSelection(matches[0].source.source_id, generated),))
                if not isinstance(generated, GeneratedAnswer) or len(generated.selections) > (9 if knowledge.generation_mode == 'grounded' else 2):
                    raise ProviderError('invalid_grounding')
                allowed = {f.source.source_id: f for f in fragments}
                for selection in generated.selections:
                    item = allowed.get(selection.source_id)
                    if item is None or (selection.content not in item.content if knowledge.generation_mode == 'grounded'
                                        else selection.content != item.content):
                        raise ProviderError('invalid_grounding')
                    if knowledge.generation_mode == 'extractive' and needs_display_text(knowledge):
                        validate_display_text(selection.display_text, selection.content, language=language)
                if generated.selections and knowledge.generation_mode == 'extractive' and needs_display_text(knowledge):
                    if len({s.source_id for s in generated.selections}) != len(generated.selections):
                        raise ProviderError('invalid_response_style')
                    validate_style('\n\n'.join(s.display_text for s in generated.selections), language=language,
                                   sentences=knowledge.response_sentences)
            except ProviderError as error:
                if error.code != 'invalid_grounding' and not (natural and error.code in ('invalid_response_style', 'copied_faq')):
                    raise
                # Validation is not a technical outage. One safe, counted answer, no retry.
                logger.warning('Chatbot generation rejected reason=%s', error.code)
                if natural:
                    generated = GeneratedAnswer((), '', (), False)
                else:
                    generated = (GeneratedAnswer((), '', (), False) if needs_display_text(knowledge) else
                                 GeneratedAnswer(tuple(EvidenceSelection(f.source.source_id, f.content) for f in fragments[:2])))
                    knowledge = replace(knowledge, generation_mode='extractive')
            # Literal selections retain the source's language. Apply language and
            # sentence-count checks to generated prose and requested presentations.
            check_presentation = knowledge.generation_mode == 'grounded' or needs_display_text(knowledge)
            if knowledge.generation_mode == 'grounded':
                slot = pending[0][2]
                replies[slot] = generated.text if generated.sufficient else local_text('no_evidence', language)
                for _, _, other in pending[1:]:
                    replies[other] = ''
                for selection in generated.selections:
                    resolved = next(r for r, b, _ in pending if any(f.source.source_id == selection.source_id for f in b.fragments))
                    records.append((resolved, (allowed[selection.source_id],), selection.content))
                routes.append(('deterministic' if locally_formatted else 'generation') if generated.sufficient else 'no_evidence')
                generated = GeneratedAnswer(())  # Already rendered; skip the extractive path.
                pending = []
            if pending:
                allowed = {f.source.source_id: f for f in fragments}
                output = list(dict.fromkeys(allowed[s.source_id] for s in generated.selections))
                grouped = {}
                by_id = {s.source_id: s for s in generated.selections}
                for item in output:
                    resolved, _, slot = next((r, b, slot) for r, b, slot in pending
                                             if any(f.source.source_id == item.source.source_id for f in b.fragments))
                    grouped.setdefault(slot, []).append(by_id[item.source.source_id].display_text
                                                       if needs_display_text(knowledge) else item.content)
                    records.append((resolved, (item,), item.content))
                for _, _, slot in pending:
                    replies[slot] = ('\n\n'.join(grouped[slot]) if slot in grouped
                                     else local_text('no_evidence', language))
                routes.append(('deterministic' if locally_formatted else 'generation') if output else 'no_evidence')
        references = []
        revalidated = {}
        for resolved, used, text in records:
            # Requery before exposing/persisting the answer. A changed/deleted/unpublished
            # source invalidates the selection; no old norm is read from the chat history.
            if resolved not in revalidated:
                revalidated[resolved] = retrieve(resolved, preferred_titles[resolved])
            fresh = revalidated[resolved]
            current = {f.source.source_id: f for f in fresh.fragments}
            for item in used:
                now = current.get(item.source.source_id)
                if (now is None or now.approval != 'approved' or now.content_hash != item.content_hash
                        or now.source.source_version != item.source.source_version
                        or now.source.public_url != item.source.public_url or not valid_public_url(now)):
                    return AssistantReply(local_text('no_evidence', language), route='no_evidence', outcome='no_evidence')
                references.append(ValidatedReference(
                    now.source.source_id, now.source.public_url, now.content_hash, text,
                    now.source.source_type, now.title, now.source.source_version,
                    (sha256(text.encode('utf-8')).hexdigest(),)))
        route = routes[0] if len(set(routes)) == 1 else 'mixed'
        outcome = ('conflict' if 'conflict' in routes else 'clarification' if 'clarification' in routes
                   else 'grounded' if any(r in ('deterministic', 'generation') for r in routes) else 'no_evidence')
        text = '\n\n'.join(r for r in replies if r)
        if check_presentation and ('generation' in routes or locally_formatted):
            validate_style(text, language=language, sentences=requested_sentences(question))
        return AssistantReply(text, tuple(dict.fromkeys(references)), route, outcome)
