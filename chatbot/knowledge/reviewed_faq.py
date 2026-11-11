"""Reviewed public answers, tied to the complete FAQ revision that supports them."""
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256

from .normalization import fold, is_platform_overview, terms_for, words
from .publication import BICYCLE, SMALL, SMALL_APPLIANCE, SPORTING, admission_is_general, admission_subject
from .registry import official_faq


@dataclass(frozen=True)
class ReviewedFAQ:
    title: str
    revision: str
    es: str
    en: str


# A revision changes if the public wording changes, including a new condition.
# Line endings and HTML whitespace do not change the meaning of the evidence.
def revision_for(title, content):
    normalized = ' '.join(unicodedata.normalize('NFC', f'{title} {content}').split())
    return sha256(normalized.encode('utf-8')).hexdigest()


FAQS = {
    'overview': ReviewedFAQ(
        '¿Qué es TRUEC@?', 'a54b103a5d99785532f357193b016367695ebc479017e555ccfb14727f5d4b20',
        'TRUEC@ es un programa del Cabildo de Tenerife para dar una segunda vida a los objetos. '
        'Puedes ofrecer lo que ya no usas y buscar o reservar artículos en el catálogo.',
        'TRUEC@ is a programme run by the Tenerife Island Council to give items a second life. '
        'You can offer things you no longer use and browse or reserve items in the catalogue.'),
    'registration': ReviewedFAQ(
        '¿Cómo me registro?', 'bef81ffab99080ce2d0886e477fc347d08b6319f51c93f5c0d50c71cd4b18bf2',
        'Abre «Regístrate» y completa el formulario. Luego confirma tu dirección con el enlace que te llegará por correo. '
        'Con el correo validado, ya podrás añadir productos y reservar artículos del catálogo.',
        'Open the registration page and fill in the form. Follow the link in the confirmation email to verify your address. '
        'Once verified, you can add products and reserve items from the catalogue.'),
    'payment': ReviewedFAQ(
        '¿Tengo que pagar algo?', '4e6d79b665f6386e8e2f98767dff72344056398bbeb22d3c5e2efb042929dbb7',
        'No se paga con dinero. Al registrarte puedes acumular puntos y usarlos para conseguir artículos.',
        'There is no payment in money. Registered users can earn points and spend them on items.'),
    'points': ReviewedFAQ(
        '¿Cómo consigo puntos?', '81a8e7029800748688c1513fa7154f2afe664155c7c37d7c90595e12a90e7a98',
        'El registro en la web y la entrega de un objeto suman 100 puntos cada uno. Retirar un artículo consume 50 puntos.',
        'Signing up on the website and dropping off an item each earn 100 points. Picking up an item costs 50 points.'),
    'publication': ReviewedFAQ(
        '¿Cómo añado un producto?', '60f2535256d24e96af2413b804edd722b2e8b3acfa3e5e216fbd970fe12ee6b5',
        'En «Añadir productos», publica el artículo con una foto y una descripción. Después entrégalo en alguno '
        'de los 8 centros TRUEC@ que hay en puntos limpios fijos de Tenerife. Muestra tu DNI o NIE al personal, '
        'que validará el artículo antes de que quede disponible para otras personas.',
        'Under “Add products”, post the item with a photo and description. Then take it to one of the 8 TRUEC@ centres '
        'at Tenerife’s fixed recycling points and show your DNI or NIE there. Staff must validate it before others can get it.'),
    'reservation': ReviewedFAQ(
        '¿Cómo reservo un objeto?', '112013c1bed188d5ec49999dca8373d3ac441aee4b81bc41f8206512ffdda8c1',
        'Elige el artículo en el catálogo y haz la reserva desde allí. Tienes 7 días para ir al punto limpio que tiene el objeto.',
        'Choose an item in the catalogue and reserve it there. You have 7 days to collect it from the recycling point that holds it.'),
    'pickup_deadline': ReviewedFAQ(
        '¿cuánto tiempo tengo para recoger un objeto reservado?', '8223e59d06579b527574913445dadb2264e7669461d0aa3dc3bc22ad30283846',
        'La FAQ indica un plazo de 7 días naturales a partir de la reserva. Recoge el artículo en el centro o almacén TRUEC@ que le corresponda.',
        'The FAQ gives you 7 calendar days from the date you reserve it. Pick it up at the TRUEC@ centre or warehouse assigned to the item.'),
    'missed_pickup': ReviewedFAQ(
        '¿Qué sucede si no recojo el objeto reservado?', 'f23b7e4bd819f9fbff62f6f4285e4c26ce328f66c89ce8fe46e27443ab11e95c',
        'Si vence el plazo sin que hayas recogido el artículo o cancelado la reserva, tendrás que esperar 7 días '
        'para hacer otra reserva. El artículo volverá a ofrecerse a otras personas.',
        'If the deadline passes and you have neither collected the item nor cancelled, you will have to wait 7 days '
        'before making another reservation. Other users will be able to reserve the item.'),
    'exchange_locations': ReviewedFAQ(
        '¿Dónde puedo intercambiar mis productos?', '56c31aa6a38dfc3c58625ff70ec0883a8bf28aec470622659193d1eff6e735d2',
        'Puedes hacer la entrega en cualquiera de los 8 centros TRUEC@ de los puntos limpios de Tenerife. '
        'Busca el más cercano en «Localizaciones». El centro recibe el artículo y te asigna puntos para el catálogo. '
        'El intercambio no requiere un trato ni un pago entre particulares.',
        'You can drop items off at any of the 8 TRUEC@ centres at Tenerife’s recycling points. '
        'Find the nearest one under “Locations”. The centre takes the item and assigns points you can use in the catalogue. '
        'There is no direct trade or payment between individuals.'),
    'pickup_center': ReviewedFAQ(
        '¿Puedo recoger un producto en cualquier centro TRUEC@?', '789c0de4eb0a14f3889710f1cf758c175c0191e5045dbfa5b39dec0e6d5be0c5',
        'Para recoger un artículo, mira en su ficha qué centro o almacén tiene asignado y acude durante su horario.',
        'Check the item listing for its assigned centre or warehouse, then collect it during that location’s opening hours.'),
    'reservation_limit': ReviewedFAQ(
        '¿Puedo reservar varios objetos?', 'd2f43769779940f1aa8f6b04c90049d522f2c8d2aa6056ffcd4bef3f90ab05b2',
        'Puedes reservar y recoger hasta 5 productos al mes en cada punto limpio, con un tope de 10 al mes entre todos '
        'los centros. Un conjunto que se ofrece como una sola unidad cuenta como un producto.',
        'You can reserve and collect up to 5 items a month per recycling point, with a total cap of 10 per month across the network. '
        'A set offered as a single unit counts as one item.'),
    'delivery_limit': ReviewedFAQ(
        '¿Cuántos objetos puedo depositar?', '2fce672299adbb0beb635a355e305a31685de0a76a162ae67a59ac8a16ac9d1e',
        'El tope es de 10 productos entregados al mes. Los productos que otras personas retiren quedan fuera de ese cómputo. '
        'Un conjunto que se ofrece como una sola unidad cuenta como un producto.',
        'The drop-off limit is 10 items per month. Items other users pick up do not count towards that limit. '
        'A set offered as a single unit counts as one item.'),
    'vehicle': ReviewedFAQ(
        '¿Puedo acceder al punto limpio con mi vehículo?', '2aea3dbcc1746272f78a29e897749635c725fb11132d7550e7e2dbc499dc0cb1',
        'Puedes entrar con tu vehículo para cargar o descargar. Permanece en el punto limpio solo mientras dure esa tarea.',
        'You may drive in to load or unload items. Stay at the recycling point only as long as that takes.'),
    'admission': ReviewedFAQ(
        '¿Qué productos puedo llevar al centro TRUEC@?', '81c9c8d4ad05d06fb3397a52dda958da1719662a1b5bd7e1774ad3a727c388e3',
        'Entre los artículos que puedes llevar hay material deportivo, juguetes, accesorios infantiles y objetos de casa. '
        'El personal del punto limpio comprueba si cada artículo es apto.',
        'Examples of items you can bring include sports equipment, toys, children’s accessories and household items. '
        'Staff at the recycling point check each item’s suitability.'),
    'exclusions': ReviewedFAQ(
        '¿Qué productos NO puedo llevar al centro TRUEC@?', '2eef6fb75dde250b123442e5e8547ce8845a6fd39b306badb7eb129d52447aa3',
        'Quedan fuera la ropa, los zapatos, los residuos de aparatos eléctricos o electrónicos, los electrodomésticos grandes, '
        'la comida, las bebidas y las plantas. Tampoco se admiten objetos que superen los 50 × 50 × 50 cm. '
        'Los pequeños electrodomésticos pueden dar puntos al entregarlos, pero el personal los gestiona fuera del catálogo.',
        'Clothing, footwear, waste electrical or electronic equipment, large appliances, food, drinks and plants are excluded. '
        'Items larger than 50 × 50 × 50 cm are also excluded. Small appliances can earn points at drop-off, but staff handle them outside the catalogue.'),
    'cancel': ReviewedFAQ(
        '¿Puedo cancelar una reserva y volver a reservar?', '0dac194d1587c044b6a8d8652f2dc3042a4af67c6cac328f97bcc3335c53a4bb',
        'Si no llegas a recoger el artículo en los 7 días, puedes cancelar la reserva. '
        'Puedes reservarlo de nuevo si todavía está disponible, una sola vez para ese artículo.',
        'If you cannot collect the item within 7 days, you can cancel. '
        'You can book it again if it is still available, once per item.'),
    'pickup': ReviewedFAQ(
        '¿Cómo recoger un producto reservado?', '18b71a76204cf1e9d141e3d0438bce5e764614a75f5bd832cd8741487cda03c5',
        'Con el correo de confirmación, ve al punto limpio o almacén que tiene el artículo. '
        'Allí te pedirán el DNI para comprobar tu identidad antes de entregártelo.',
        'Once you have the confirmation email, go to the recycling point or warehouse that holds the item. '
        'Staff will check your DNI before handing it over.'),
}

# Narrow answers can use the same FAQ without showing irrelevant rules about other items.
VARIANTS = {
    'registration_points': ('points', 'El registro en la web suma 100 puntos.',
                            'Signing up on the website earns 100 points.'),
    'delivery_points': ('points', 'La entrega de un objeto suma 100 puntos.',
                        'Dropping off an item earns 100 points.'),
    'publication_points': ('points', 'Los puntos por aportar un objeto se conceden al entregarlo. '
                            'Publicarlo por sí solo no suma puntos.',
                           'You earn points for contributing an item when you drop it off. Posting it alone earns no points.'),
    'publication_photos': ('publication',
                           'Para publicar, acompaña el artículo con una foto y una descripción en «Añadir productos».',
                           'To post the item, include a photo and description under “Add products”.'),
    'publication_delivery': ('publication',
                              'Una vez publicado, entrégalo en alguno de los 8 centros TRUEC@ que hay en puntos limpios '
                              'fijos de Tenerife. Allí, muestra tu DNI o NIE para que el personal pueda validar el artículo.',
                              'After posting it, take it to one of the 8 TRUEC@ centres at Tenerife’s fixed recycling points. '
                              'Show your DNI or NIE so staff can validate the item.'),
    'bicycle': ('admission',
                'Una bicicleta puede encajar como material deportivo. El personal del punto limpio confirma si es apta.',
                'A bicycle can fall under sports equipment. Staff at the recycling point confirm whether it is suitable.'),
    'sport': ('admission',
              'Puedes llevar material deportivo. El personal del punto limpio comprueba si el artículo es apto.',
              'You can bring sports equipment. Staff at the recycling point check whether the item is suitable.'),
    'toy': ('admission',
            'Los juguetes están entre los artículos que puedes llevar. El personal del punto limpio confirma si el juguete es apto.',
            'Toys are one of the categories you can bring. Staff at the recycling point confirm whether the toy is suitable.'),
    'clothing': ('exclusions', 'La ropa queda fuera del programa TRUEC@.', 'TRUEC@ does not accept clothing.'),
    'footwear': ('exclusions', 'El calzado queda fuera del programa TRUEC@.', 'TRUEC@ does not accept footwear.'),
    'food': ('exclusions', 'Los alimentos quedan fuera del programa TRUEC@.', 'TRUEC@ does not accept food.'),
    'appliance': ('exclusions',
                  'Si es un electrodoméstico pequeño y cabe dentro del límite de 50 × 50 × 50 cm, puedes entregarlo '
                  'para obtener puntos. Lo gestiona personal autorizado y queda fuera del catálogo.',
                  'A small appliance that fits within the 50 × 50 × 50 cm limit can earn points at drop-off. '
                  'Authorised staff handle it outside the catalogue.'),
    'charge': ('points', 'El descuento de 50 puntos se aplica al retirar el artículo.',
               'The 50 points are deducted when you collect the item.'),
}


def reviewed_intent(question, topic, *, intent='', original=''):
    text = ' '.join(words(question))
    terms = set(terms_for(text))
    if intent == 'platform.overview' or is_platform_overview(question):
        return 'overview'
    if intent:
        return {'points.registration': 'registration_points', 'points.accepted_delivery': 'delivery_points',
                'points.publication': 'publication_points'}.get(intent, '')
    if topic == 'publication':
        followup = ' '.join(words(original))
        if re.fullmatch(r'(?:y |and )?(?:(?:las )?fotos|(?:the )?photos)', followup):
            return 'publication_photos'
        if re.fullmatch(r'(?:y |and )?(?:despues|luego|after that|then)', followup):
            return 'publication_delivery'
        return 'publication'
    if topic == 'admission':
        subject = set(admission_subject(question))
        if admission_is_general(question):
            return 'exclusions' if re.search(r'\b(?:no|not|excluded|forbidden)\b', text) else 'admission'
        # Require the entire subject, so modifiers such as "broken" cannot be ignored.
        for key, known in (
            ('bicycle', BICYCLE), ('sport', SPORTING), ('toy', {'juguete', 'juguetes'}),
            ('clothing', {'ropa', 'prendas', 'clothing', 'clothes'}),
            ('footwear', {'calzado', 'zapato', 'zapatos', 'footwear', 'shoes'}),
            ('food', {'comida', 'alimento', 'alimentos', 'food'}),
        ):
            if subject and subject <= known:
                return key
        appliance = SMALL_APPLIANCE | {'electrodomestico'}
        if subject <= appliance | SMALL and (subject & SMALL_APPLIANCE or subject & appliance and subject & SMALL):
            return 'appliance'
        return ''
    if topic == 'reservation':
        if re.search(r'\b(?:no (?:(?:lo|la) )?recoj\w*|don t (?:collect|pick up)|miss (?:the )?(?:pickup|deadline))\b', text):
            return 'missed_pickup'
        if 'cancelar' in terms:
            return 'cancel'
        if re.search(r'\b(?:cuanto tiempo|how long|plazo|deadline|caduca\w*)\b', text):
            return 'pickup_deadline'
        if re.search(r'\b(?:varios|cuantos|multiple|how many)\b', text):
            return 'reservation_limit'
        if re.search(r'\b(?:donde|where|cualquier centro|any centre|any center)\b', text):
            return 'pickup_center'
        if re.search(r'\b(?:como|how)\b', text):
            return 'pickup' if 'recogida' in terms else 'reservation'
    if re.search(r'\b(?:cuantos objetos|how many items)\b', text) and re.search(r'\b(?:depositar|entregar|drop off)\b', text):
        return 'delivery_limit'
    if re.search(r'\b(?:donde|where)\b', text) and re.search(r'\b(?:intercambiar|exchange)\b', text):
        return 'exchange_locations'
    if topic == 'center':
        if re.search(r'\b(?:vehiculo|coche|car|vehicle)\b', text):
            return 'vehicle'
        if 'recogida' in terms and re.search(r'\b(?:cualquier centro|any centre|any center)\b', text):
            return 'pickup_center'
    if re.search(r'\b(?:como me registro|como registrarme|how (?:do i |to )?(?:register|sign up))\b', text):
        return 'registration'
    if re.search(r'\b(?:tengo que pagar|hay que pagar|do i (?:have to |need to )?pay)\b', text):
        return 'payment'
    if 'puntos' in terms and re.search(r'\b(?:resta\w*|descuent\w*|deduct\w*)\b', text):
        return 'charge'
    if re.search(r'\b(?:como (?:consigo|obtengo|gano) puntos|how (?:do i |to )?earn points)\b', text):
        return 'points'
    return ''


def definition(key):
    variant = VARIANTS.get(key)
    return FAQS.get(variant[0] if variant else key)


def reviewed_answer(key, fragments, language):
    definition_for_key = definition(key)
    if definition_for_key is None:
        return None
    matches = [f for f in fragments if official_faq(f) and fold(f.title) == fold(definition_for_key.title)]
    if len(matches) != 1:
        return None
    source = matches[0]
    if revision_for(source.title, source.content) != definition_for_key.revision:
        return None
    _, es, en = VARIANTS.get(key, ('', definition_for_key.es, definition_for_key.en))
    return (en if language.startswith('en') else es), source
