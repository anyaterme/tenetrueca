"""Only approved facts. Read constants, never balances, ledgers or mutating services."""
from dataclasses import dataclass

from points import services as point_rules

from ..contracts import RuleFact
from ..normalization import lexical_score
from ..registry import APPROVED_CLAIMS
from .base import fragment


@dataclass(frozen=True)
class RuleDefinition:
    claim: str
    title: str
    content: str
    value: int | bool
    event: str
    scope: str
    conditions: tuple[str, ...]
    constant: str | None = None
    english: str = ''


# Content is approved explanatory wording, not a duplicate database corpus. Numbers are
# guarded against drift in their actual constants; a changed number needs explicit approval.
RULES = (
    # Descriptive capabilities already verified for Phase 1 and on the public portal.
    # No deadlines, prices, admission conditions or promise of acceptance.
    RuleDefinition('platform.overview', 'Cómo funciona TRUEC@',
                   'TRUEC@ es una plataforma de intercambio sostenible en Tenerife para dar una segunda vida a los objetos.\n\n'
                   'Puedes consultar el catálogo y reservar objetos que te interesen, o publicar los que ya no utilizas '
                   'para que el equipo los revise. Publicar no garantiza su aceptación.\n\n'
                   '¿Quieres que te explique cómo publicar un objeto o cómo hacer una reserva?',
                   True, 'public_platform_description', 'platform', (),
                   english='TRUEC@ is a sustainable exchange platform in Tenerife that gives objects a second life.\n\n'
                   'You can browse the catalog and reserve objects that interest you, or submit objects you no longer use '
                   'for the team to review. Publishing does not guarantee acceptance.\n\n'
                   'Would you like to know how to publish an object or make a reservation?'),
    RuleDefinition('points.registration', 'Puntos por registro público',
                   'Recibes {value} puntos cuando completas el registro público y activas tu cuenta.',
                   100, 'public_registration_completed', 'registration',
                   ('Registro público completado y cuenta activada.',), 'REGISTRATION_POINTS',
                   'You receive {value} points when you complete public registration and activate your account.'),
    RuleDefinition('points.accepted_delivery', 'Puntos por entrega físicamente aceptada',
                   'Recibes {value} puntos cuando el personal autorizado acepta físicamente el objeto entregado.',
                   100, 'physical_delivery_accepted', 'delivery',
                   ('La entrega debe ser aceptada físicamente por personal autorizado.',), 'ACCEPTED_OBJECT_POINTS',
                   'You receive {value} points when authorized staff physically accept the delivered object.'),
    RuleDefinition('points.publication', 'Publicar no concede puntos por sí mismo',
                   'No. Publicar un objeto no genera puntos por sí mismo; se conceden cuando el personal autorizado acepta físicamente la entrega.',
                   False, 'publication_created', 'publication',
                   ('Publicar o recibir una evaluación no sustituye la aceptación física.',),
                   english='No. Publishing an object does not itself award points; points are awarded when authorized staff physically accept the delivery.'),
    RuleDefinition('moderation.human_decision', 'Moderación y decisión humana',
                   'La moderación asistida por IA es informativa y no sustituye la decisión del personal autorizado.',
                   True, 'human_moderation_decision', 'moderation',
                   ('La IA no aprueba ni rechaza publicaciones.',),
                   english='AI-assisted moderation is informative and does not replace the decision of authorized staff.'),
)


def retrieve(query, now, *, include_overview=False):
    fragments, facts, conflicts = [], [], []
    for rule in RULES:
        if rule.claim not in APPROVED_CLAIMS:
            continue
        if rule.claim == 'platform.overview' and not include_overview:
            continue
        content = rule.content.format(value=rule.value)
        score = lexical_score(query, rule.title, content)
        if rule.claim == 'platform.overview':
            score = max(score, 1)  # An explicitly recognized ES/EN identity question.
        if score <= 0:
            continue
        if rule.constant and getattr(point_rules, rule.constant) != rule.value:
            conflicts.append(f'approved_rule_drift:{rule.claim}')
            continue
        source_id = f'business_rule:{rule.claim}'
        fragments.append(fragment(
            'business_rule', rule.claim, rule.title, content, url=None,
            updated_at=None, now=now, relevance=score, method='structured', claims=(rule.claim,),
        ))
        facts.append(RuleFact(rule.claim, rule.value, 'points' if type(rule.value) is int else None,
                              rule.event, rule.scope, rule.conditions, (source_id,),
                              localized_content=(('es', content), ('en', rule.english.format(value=rule.value)))))
    return fragments, facts, conflicts
