"""Small deterministic continuity rules. Assistant messages never establish facts/entities."""
import re
from dataclasses import dataclass

from django.conf import settings

from locations.services import public_centers

from .knowledge.conflicts import unsafe_content
from .knowledge.normalization import fold, is_platform_overview, terms_for, words


@dataclass(frozen=True)
class ResolvedQuestion:
    original: str
    search_query: str
    topic: str = ''
    center_id: str = ''
    intent: str = ''
    clarification: str = ''


def split_questions(question):
    # Only explicit, independent interrogative clauses; never split if/except conditions.
    parts = re.split(r'\?\s*(?=\S)|\s+(?:y|and)\s+(?=[¿]?(?:cu[aá]nt\w*|cu[aá]ndo|c[oó]mo|how|when)\b)',
                     question, flags=re.IGNORECASE)
    parts = tuple(p.strip(' ¿?') for p in parts if p.strip(' ¿?'))
    return parts if len(parts) <= 2 else ()


def topic_for(text):
    terms = set(terms_for(text))
    if terms.intersection({'centro', 'limpio', 'horario', 'telefono', 'direccion'}):
        return 'center'
    if terms.intersection({'reserva', 'recogida', 'cancelar', 'cancelarla', 'cancelarlas'}):
        return 'reservation'
    if terms.intersection({'puntos', 'registro', 'entrega'}):
        return 'points'
    if terms.intersection({'moderacion', 'ia'}):
        return 'moderation'
    if terms.intersection({'categoria', 'catalogo'}):
        return 'category'
    return ''


def point_intent(text, prior_topic):
    terms = {term for term in terms_for(text) if not term.isdigit()}
    # Extra conditions or another question prevent a local rule from being presented as
    # a complete answer. This is intentionally a small supported question vocabulary.
    common = {'puntos', 'recibo', 'reciben', 'recibir', 'recibirlos', 'recibirlo',
              'recibire', 'obtengo', 'muchos', 'correctamente', 'completar', 'completo',
              'objeto', 'objetos', 'cuenta', 'gana', 'earn', 'awarded', 'award', 'awards', 'many', 'them', 'foto', 'fotografia'}
    groups = (
        ('points.registration', {'registro', 'activacion'}, {'registro', 'activacion', 'publico'}),
        ('points.accepted_delivery', {'entrega'}, {'entrega', 'aceptada', 'fisica', 'staff', 'authorized'}),
        ('points.publication', {'publicar'}, {'publicar', 'solo', 'simple', 'simply', 'itself'}),
    )
    for claim, required, allowed in groups:
        if (terms.intersection(required) and not terms.difference(common | allowed)
                and ('puntos' in terms or prior_topic == 'points')):
            return claim
    if terms and terms.issubset({'moderacion', 'ia', 'humana', 'decision', 'decide', 'sustituye',
                                'replace', 'replaces', 'staff', 'aprobacion', 'aprueba',
                                'bien', 'dice', 'dijo', 'indica', 'ya', 'aprobado', 'aprobada', 'objeto'}):
        if terms.intersection({'moderacion', 'ia'}) and len(terms) > 1:
            return 'moderation.human_decision'
    return ''


class ConversationContextResolver:
    def resolve(self, question, recent_users=()):
        if type(settings.CHATBOT_CONTEXT_TURNS) is not int or settings.CHATBOT_CONTEXT_TURNS < 1:
            raise ValueError('invalid_context_turn_limit')
        prior, previous_center, previous_name = '', '', ''
        terms = set(terms_for(question))
        current_topic = topic_for(question)
        needs_center = bool(terms.intersection({'horario', 'telefono', 'direccion'})
                            or re.search(r'\ball[ií]|\bese centro\b|\bthere\b|\bits\b', question, re.I)
                            or re.search(r'\b(donde|where)\b', fold(question))
                            and re.search(r'\b(reco[jg]\w*|collect|pickup)\b', fold(question)))
        center_rows = []
        if current_topic == 'center' or needs_center:
            center_rows = list(public_centers().values_list('id', 'name'))

        def centers_in(text):
            query_words = set(words(text))
            found = []
            for pk, name in center_rows:
                distinctive = set(words(name)).difference({'punto', 'limpio', 'de', 'del', 'la', 'las', 'el', 'los', 'of', 'centro', 'center', 'recycling', 'the'})
                if distinctive and distinctive.issubset(query_words):
                    found.append((f'center:{pk}', name))
            return found

        named = centers_in(question)
        if len(named) > 1:
            return ResolvedQuestion(question, question, 'center', clarification='center')
        for text in reversed(tuple(recent_users)[-settings.CHATBOT_CONTEXT_TURNS:]):
            if not isinstance(text, str) or len(text) > settings.CHATBOT_MAX_INPUT_CHARS:
                break
            if unsafe_content(text):
                break
            previous_topic = topic_for(text)
            if previous_topic:
                prior = previous_topic
                if prior == 'center':
                    names = centers_in(text)
                    if len(names) == 1:
                        previous_center, previous_name = names[0]
                        break
                    if not names:
                        continue
                break
        center_id, center_name = named[0] if named else ('', '')
        anaphoric_cancel = re.search(r'cancelarl[ao]s?|\bcancel\b.*\b(it|them)\b', fold(question))
        if anaphoric_cancel and prior != 'reservation' and 'reserva' not in terms:
            return ResolvedQuestion(question, question, clarification='topic')
        explicit_center = re.search(r'punto\s+limpio|recycling\s+cent(?:er|re)|centro\s+de', fold(question))
        if explicit_center and not center_id and (needs_center or re.search(r'donde|where|ubicacion|location', fold(question))):
            return ResolvedQuestion(question, question, 'center', clarification='center')
        if needs_center and not center_id and prior == 'center' and not explicit_center:
            center_id, center_name = previous_center, previous_name
        if needs_center and not center_id:
            return ResolvedQuestion(question, question, 'center', clarification='center')
        topic = current_topic
        if not topic and re.search(r'cancelarl[ao]s?|\bit\b|\bthem\b|\by\b|\band\b', fold(question)):
            topic = prior
        intent = 'platform.overview' if is_platform_overview(question) else point_intent(question, prior)
        if intent == 'platform.overview':
            topic = 'platform'
        elif intent:
            topic = 'points' if intent.startswith('points.') else 'moderation'
        if (re.match(r'\s*(¿?y\b|and\b)', fold(question)) and 'puntos' not in terms
                and terms.intersection({'entrega', 'publicar'}) and prior != 'points'):
            return ResolvedQuestion(question, question, clarification='topic')
        if re.fullmatch(r'(y |and )?(eso|all[ií]|it|that|those|lo|cuanto cuesta|how much)[ ?¿]*', fold(question)) and not prior:
            return ResolvedQuestion(question, question, clarification='topic')
        query = question
        if center_id:
            query += ' ' + center_name
        elif topic == 'reservation':
            query += ' reserva'
        elif intent and intent != 'platform.overview':
            query += ' ' + {'points.registration': 'registro puntos', 'points.accepted_delivery': 'entrega puntos',
                            'points.publication': 'publicar puntos', 'moderation.human_decision': 'moderacion humana'}[intent]
        return ResolvedQuestion(question, query[:settings.CHATBOT_MAX_INPUT_CHARS], topic, center_id, intent)
