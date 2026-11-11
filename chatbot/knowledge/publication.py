"""Route publication and admission searches by the question they need to answer."""
import re

from .normalization import terms_for, words


PUBLISH = r'public(?:ar|o|arlo|arla|acion|aciones)|(?:anad|sub)(?:ir|o|a|e)|agreg(?:ar|o)|publish(?:ing)?|add|upload|list|submit'
DELIVER = (
    r'dej(?:ar(?:lo|la)?|o|an)|llev(?:ar(?:lo|la)?|o)|entreg(?:ar(?:lo|la)?|o|a)|'
    r'deposit(?:ar|o)?|donar|intercambiar|bring|drop(?: off)?|deliver|donate|exchange'
)
ACCEPT = r'acept\w*|admi(?:t|s)\w*|permitid\w*|apt[oa]s?|accept\w*|allow\w*|eligib\w*'
PROCEDURE = re.compile(rf'\b(?:como|how)\b.{{0,35}}\b(?:{PUBLISH})\b')
ADMISSION = re.compile(
    rf'\b(?:{ACCEPT})\b|\b(?:pued[oaen]+|quiero|quisiera|podria|para|can|could|want to)\s+(?:i\s+|you\s+|to\s+)?(?:{DELIVER})\b'
    rf'|\b(?:have|got)\b.{{0,70}}\bto\s+(?:{DELIVER})\b'
    rf'|^(?:{DELIVER})\s+(?:un[ao]?|an?)\b'
)
OTHER_TOPIC = re.compile(
    r'\b(?:puntos(?! limpios?)|points|reserv\w*|recog\w*|horarios?|hours|cuantos?|cuantas?|how many|'
    r'donde|where|despues|after|plazo|deadline|estado de|status)\b'
)
GENERAL = re.compile(r'^(?:que|what|which)\s+(?:(?:tipos? de|kinds? of|types? of)\s+)?'
                     r'(?:objetos?|productos?|articulos?|cosas?|items?|objects?|products?|'
                     r'puedo|se puede|can i)\b')
FOLLOWUP = re.compile(r'(?:(?:y|and|que tal|what about)\s+|(?:un[ao]s?|an?|some)\s+)\S+(?:\s+\S+){0,4}')

# Synonyms and category search terms only. Whether any of them is admitted comes
# from the published FAQ answer, never from this table or a catalogue category.
BICYCLE = {'bicicleta', 'bicicletas', 'bici', 'bicis', 'bicycle', 'bicycles', 'bike', 'bikes'}
SPORTING = {'deportivo', 'deportiva', 'deporte', 'sporting'}
SMALL = {'pequeno', 'pequena', 'pequenos', 'pequenas', 'small'}
SMALL_APPLIANCE = {'cafetera', 'cafeteras', 'tostadora', 'tostadoras', 'batidora', 'batidoras'}
ITEM_GROUPS = (
    {'ropa', 'prendas', 'clothes', 'clothing'},
    {'calzado', 'zapato', 'zapatos', 'shoes', 'footwear'},
    {'comida', 'alimento', 'alimentos', 'food'},
    BICYCLE,
    {'juguete', 'juguetes', 'toy', 'toys'},
    SPORTING,
    SMALL,
)
CONTEXT_WORDS = frozenset(
    'tengo tiene tenemos quiero quisiera podria pueden puede puedo voy llevarlo llevarla '
    'entrega publicar objeto objetos producto productos articulo articulos cosa cosas item items '
    'have got want bring drop off donate give exchange can could would please porfavor favor '
    'intercambio sustainable truec hay hacen hacer hago bien tambien tal esto eso esa ese that '
    'sirve aqui ahi alli ahora centro limpio puntos donacion donar otro otra about'.split()
)
ITEM_NOISE = re.compile(rf'^(?:{PUBLISH}|{DELIVER}|{ACCEPT}|\d+)$')


def publication_topic(question):
    text = ' '.join(words(question))
    if OTHER_TOPIC.search(text):
        return ''
    if PROCEDURE.search(text) or re.match(rf'(?:{PUBLISH})\s+(?:un |una |an? )?(?:objeto|articulo|producto|item|object)\b', text):
        return 'publication'
    if ADMISSION.search(text):
        return 'admission'
    return ''


def admission_subject(question):
    text = ' '.join(words(question))
    if admission_is_general(question):
        return ()
    text = re.split(r'\b(?:al|a un|en el|en un|to the|at the)\s+(?:centro|punto|recycling)\b', text, maxsplit=1)[0]
    return tuple(term for term in terms_for(text)
                 if term not in CONTEXT_WORDS and not ITEM_NOISE.fullmatch(term))


def admission_is_general(question):
    return bool(GENERAL.match(' '.join(words(question))))


def admission_followup(question):
    text = ' '.join(words(question))
    return bool(FOLLOWUP.fullmatch(text) and not OTHER_TOPIC.search(text))


def _matches_item(term, available, content):
    # A bicycle can be looked up as sporting goods, but not the other way around.
    if term in BICYCLE and available.intersection(SPORTING):
        return True
    if term in SMALL_APPLIANCE:
        # Do not use a rule about large appliances as evidence about a coffee maker.
        return term in available or bool(re.search(
            r'\b(?:pequenos? electrodomesticos?|electrodomesticos? pequenos?|small appliances?)\b', content))
    return term in available or any(term in group and available.intersection(group) for group in ITEM_GROUPS)


def relevant_faq(topic, subject, title, answer):
    """A platform introduction or an unrelated procedure is not a relevant answer."""
    heading, content = ' '.join(words(title)), ' '.join(words(answer))
    if topic == 'publication':
        return bool(not OTHER_TOPIC.search(heading) and re.search(rf'\b(?:{PUBLISH})\b', heading))
    if topic == 'admission':
        if not re.search(rf'\b(?:{ACCEPT}|{DELIVER}|(?:presentar|proponer|subir|publicar))\b', content):
            return False
        if subject:
            available = set(terms_for(answer))
            return all(_matches_item(term, available, content) for term in subject)
        return bool(admission_is_general(title) and ADMISSION.search(heading))
    return True
