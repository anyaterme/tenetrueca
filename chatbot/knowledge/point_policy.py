"""Strict provenance for numeric point policy, not a general semantic verifier.

A published FAQ is not authority to alter an approved award. Numeric point-policy units
must be one complete approved verbalization; otherwise omit the whole unit. Local RuleFact
answers remain available. Public recycling-center information is outside point accounting.
"""
import re

from .normalization import fold
from .registry import APPROVED_CLAIMS


def unverified_point_policy(item):
    if item.source.source_type != 'faq':
        return False
    body = re.sub(r'\bpuntos?\s+limpios?\b', 'centros', fold(item.content))
    numeric = (re.search(r'\b(puntos|points)\b', body)
               and re.search(r'\d|\b(cien|ciento|hundred|cincuenta|fifty)\b', body))
    positive_publication = re.search(
        r'\b(publicar|publicacion|publish|publishing|publication)\s+(?:(?:un |an? )?(?:objeto|object)\s+)?'
        r'(da|genera|concede|otorga|awards?|gives?|earns?)\b.{0,35}\b(puntos|points)\b', body)
    if not numeric and not positive_publication:
        return False
    from .sources.rules import RULES, point_rules
    approved = set()
    for rule in RULES:
        if rule.claim not in APPROVED_CLAIMS or rule.constant and getattr(point_rules, rule.constant) != rule.value:
            continue
        approved.add(fold(rule.content.format(value=rule.value)).strip())
        approved.add(fold(rule.english.format(value=rule.value)).strip())
    return fold(item.content).strip() not in approved
