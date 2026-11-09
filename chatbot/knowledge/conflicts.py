"""Conservative exclusions, not an automatic semantic policy adjudicator."""
import re

from .normalization import fold, terms_for
from .registry import BLOCKED_CLAIMS


def conflicting_claims(text):
    terms = set(terms_for(text))
    normalized = fold(text)
    found = set()
    reservation_domain = bool(terms.intersection({'reserva', 'recogida'})
                              or any(term.startswith(('reserv', 'recog')) for term in terms))
    # Block claims, not the reservation topic. Requirements/cancellation can be public
    # evidence without authorizing a deadline or charging event.
    if (terms.intersection({'plazo', 'caducidad', 'expire', 'expires', 'expired', 'expiration'})
            or re.search(r'cuanto\s+(tiempo|tardo)|how\s+long|no\s+(recojo|recoger)|don.t\s+(collect|pick)', normalized)
            or reservation_domain and re.search(r'\b(?:7|siete|seven|72)\s+(dias|days|horas|hours)\b', normalized)):
        found.add('reservation.pickup_window')
    if (terms.intersection({'cargo', 'cobro', 'cobrar', 'descontar'})
            or any(term.startswith(('descont', 'cobr')) for term in terms)):
        found.add('points.reservation_charge')
    if re.search(r'\b(?:50|cincuenta|fifty)\s+(puntos|points)\b', normalized):
        found.add('points.reservation_charge')
    if (terms.intersection({'electrodomestico', 'cafetera', 'tostadora', 'batidora', 'plancha'})
            or {'pequeno', 'electrico'}.issubset(terms)
            or {'small', 'electrical'}.issubset(terms)):
        found.add('admission.small_appliance')
    return tuple(sorted(found.intersection(BLOCKED_CLAIMS)))


def unsafe_content(text):
    """Reject obvious instruction/active markup patterns; no text is ever executed.

    This is defense in depth, not a guarantee against every paraphrase of prompt injection.
    A future generator must keep all retrieved content at data privilege.
    """
    normalized = fold(text)
    return bool(re.search(
        r'ignore.{0,60}(instructions|rules|system)|'
        r'ignora.{0,60}(instrucciones|reglas|sistema)|'
        r'(system|developer|assistant|sistema|desarrollador)\s*:|'
        r'(revela|reveal|show|muestra).{0,60}(secret|clave|api.key|password|contrasena)|'
        r'(consulta|query|select|accede).{0,60}(accounts|usuarios|users|database|base de datos)|'
        r'<\s*[a-z/!]|javascript\s*:|data\s*:',
        normalized, flags=re.DOTALL,
    ))
