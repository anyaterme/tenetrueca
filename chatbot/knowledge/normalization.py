"""Bounded, literal queries with curated equivalents; no SQL/tsquery from user input."""
import re
import unicodedata
from dataclasses import dataclass


ALIASES = {
    'points': 'puntos', 'punto': 'puntos',
    'registration': 'registro', 'register': 'registro', 'signup': 'registro',
    'registrarme': 'registro', 'registrarse': 'registro', 'registrar': 'registro',
    'registrame': 'registro',  # Common omitted-r typo; never a general fuzzy norm matcher.
    'activation': 'activacion', 'activate': 'activacion',
    'verificar': 'activacion', 'verificacion': 'activacion', 'verify': 'activacion',
    'delivery': 'entrega', 'deposit': 'entrega', 'entregar': 'entrega', 'entregado': 'entrega',
    'accepted': 'aceptada', 'aceptado': 'aceptada', 'fisicamente': 'fisica', 'physically': 'fisica',
    'publish': 'publicar', 'publishing': 'publicar', 'publication': 'publicar', 'publicacion': 'publicar',
    'publicaciones': 'publicar', 'publicarlo': 'publicar', 'publico': 'publicar',
    'entregarlo': 'entrega', 'entrego': 'entrega',
    'delivering': 'entrega', 'object': 'objeto', 'objects': 'objeto',
    'diarias': 'diario', 'daily': 'diario', 'cancel': 'cancelar',
    'cancelarla': 'cancelar', 'cancelarlas': 'cancelar',
    'moderation': 'moderacion', 'review': 'revision', 'approval': 'aprobacion',
    'human': 'humana', 'humano': 'humana', 'ai': 'ia',
    'categories': 'categoria', 'category': 'categoria', 'categorias': 'categoria',
    'catalog': 'catalogo', 'catalogue': 'catalogo',
    'recycling': 'limpio', 'limpios': 'limpio', 'centers': 'centro', 'centres': 'centro',
    'center': 'centro', 'centre': 'centro', 'centros': 'centro',
    'opening': 'horario', 'hours': 'horario', 'horarios': 'horario', 'schedule': 'horario',
    'address': 'direccion', 'phone': 'telefono', 'contact': 'contacto',
    'sports': 'deporte', 'sport': 'deporte', 'deportivos': 'deporte', 'deportivas': 'deporte',
    'toys': 'juguetes', 'toy': 'juguetes', 'household': 'hogar',
    'reservation': 'reserva', 'reservations': 'reserva', 'reservar': 'reserva',
    'reservas': 'reserva', 'booking': 'reserva', 'book': 'reserva',
    'pickup': 'recogida', 'collection': 'recogida', 'collect': 'recogida', 'recoger': 'recogida',
    'retirar': 'recogida', 'retirada': 'recogida',
    'deadline': 'plazo', 'expiry': 'plazo', 'vencimiento': 'plazo',
    'charge': 'cargo', 'charged': 'cargo', 'deducted': 'cargo', 'deduct': 'cargo',
    'descuento': 'cargo', 'descuentan': 'cargo', 'cobran': 'cargo', 'cost': 'cargo',
    'costs': 'cargo', 'coste': 'cargo', 'cuesta': 'cargo',
    'appliance': 'electrodomestico', 'appliances': 'electrodomestico',
    'electrodomesticos': 'electrodomestico', 'coffee': 'cafetera', 'toaster': 'tostadora',
}
STOP_WORDS = frozenset('a al algo an and are as at be can como con cuando cuanto cuantos de '
                       'del do does el ella en es esta este for from has how i in is it la las '
                       'lo los me mi mis my no o of on or para por puedo que se si son su the '
                       'their to trueca un una unos unas what when where which with y yo '
                       'you your dan da obtener get give receive gano ganar'.split())


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c) and unicodedata.category(c) != 'Cf')


def words(text):
    return re.findall(r'[a-z0-9]+', fold(text))


def is_platform_overview(text):
    """Only a general, explicitly named platform question; never absorb extra conditions."""
    normalized = ' '.join(words(text))
    brand = r'(?:trueca|truec)'
    return bool(re.fullmatch(
        rf'(?:(?:como funciona|que es) (?:la plataforma )?{brand}'
        rf'|how does {brand} work|what is {brand})', normalized))


def terms_for(text):
    return tuple(dict.fromkeys(ALIASES.get(w, w) for w in words(text) if w not in STOP_WORDS))


@dataclass(frozen=True)
class KnowledgeQuery:
    terms: tuple[str, ...]
    language: str
    literal_terms: tuple[str, ...] = ()


def normalize_query(question, *, language, max_chars):
    if not isinstance(question, str) or not question.strip() or '\x00' in question:
        raise ValueError('invalid_knowledge_query')
    if len(question) > max_chars:
        raise ValueError('knowledge_query_too_long')
    try:
        question.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid_knowledge_query') from None
    language = str(language).lower().replace('_', '-').split('-', 1)[0]
    terms = terms_for(question)[:24]
    # Keep literal accents for native FTS dictionaries as well as folded equivalents.
    # For example Spanish documentación and folded documentacion need not stem alike.
    literal = tuple(dict.fromkeys(
        word for word in re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', question.casefold()))
        if ALIASES.get(fold(word), fold(word)) in terms
    ))[:24]
    return KnowledgeQuery(terms, language if language in ('es', 'en') else 'es', literal)


def lexical_score(query, title, content):
    if not query.terms:
        return 0.0
    wanted = set(query.terms)
    return (3 * len(wanted.intersection(terms_for(title)))
            + len(wanted.intersection(terms_for(content)))) / len(wanted)
