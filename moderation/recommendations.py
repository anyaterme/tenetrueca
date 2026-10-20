"""Editable moderation drafts derived locally from the current saved AI evaluation."""
from assistant.schema import CHECK_LABELS
from moderation.models import ModerationDecision


def _bounded_text(items, limit):
    parts = []
    for item in items:
        text = ' '.join(item.split())
        if text and text not in parts:
            parts.append(text)
    text = '\n'.join(parts)
    return text if len(text) <= limit else text[:limit - 1].rsplit(' ', 1)[0] + '…'


def decision_proposal(evaluation, *, choice=None):
    """The caller supplies a current evaluation; no provider or administrative action is invoked."""
    if not evaluation or evaluation.status != 'completed' or not evaluation.result:
        return None
    result = evaluation.result
    checks = result.get('checks', {})
    if set(checks) != set(CHECK_LABELS) or any(
        check.get('status') not in ('CORRECTO', 'INCIDENCIA', 'NO_VERIFICABLE')
        for check in checks.values()
    ):
        return None

    incidents = [key for key in CHECK_LABELS if checks[key]['status'] == 'INCIDENCIA']
    unknown = [key for key in CHECK_LABELS if checks[key]['status'] == 'NO_VERIFICABLE']
    needs_physical_check = result.get('dimensions', {}).get('physical_verification', True)
    if 'prohibited_items' in incidents:
        decision = ModerationDecision.Decision.REJECT
        reasons = ['La evaluación señala que el artículo no está admitido en el catálogo.',
                   checks['prohibited_items']['explanation']]
        if 'admission' in incidents:
            reasons.append(checks['admission']['explanation'])
        directions = ['No publiques este artículo en el catálogo de intercambio. '
                      'Consulta con el personal las opciones de entrega aplicables.']
    elif (not incidents and not unknown and not needs_physical_check
          and result.get('overall') == 'apparently_suitable'
          and not result.get('issues') and not result.get('unverifiable')):
        decision = ModerationDecision.Decision.APPROVE
        reasons = ['La evaluación no detecta incidencias ni aspectos pendientes de verificar.']
        directions = ['Confirma que la información y las fotografías corresponden al artículo '
                      'antes de registrar la aprobación.']
    else:
        decision = ModerationDecision.Decision.REQUEST_CHANGES
        reasons = ['La evaluación requiere cambios o información adicional antes de decidir.']
        reasons.extend(f'{CHECK_LABELS[key]}: {checks[key]["explanation"]}' for key in incidents[:3])
        if unknown:
            reasons.append('Pendiente de verificar: ' + ', '.join(CHECK_LABELS[key] for key in unknown) + '.')
        if not incidents:
            reasons.extend(result.get('issues', [])[:2])
        directions = list(result.get('recommendations', []))
        if not directions:
            if checks['description']['status'] != 'CORRECTO':
                directions.append('Completa la descripción con el estado real del artículo y las piezas '
                                  'o accesorios que se entregan.')
            if checks['photo_quality']['status'] != 'CORRECTO':
                directions.append('Añade fotografías propias y claras del artículo real, su parte trasera '
                                  'y los accesorios incluidos.')
            if checks['dimensions']['status'] != 'CORRECTO' or needs_physical_check:
                limit = result.get('dimensions', {}).get('limit_cm', 50)
                directions.append(f'Comprueba físicamente alto, ancho y fondo en centímetros '
                                  f'y verifica el límite de {limit} cm por eje.')
        if not directions:
            directions.append('Revisa las incidencias y completa los datos pendientes indicados '
                              'en la evaluación antes de volver a enviar la publicación.')

    recommended = decision
    if choice is not None and choice != recommended:
        decision = ModerationDecision.Decision(choice)
        findings = [f'{CHECK_LABELS[key]}: {checks[key]["explanation"]}' for key in incidents[:3]]
        if unknown:
            findings.append('Pendiente de verificar: ' + ', '.join(CHECK_LABELS[key] for key in unknown) + '.')
        if not findings:
            findings.extend(result.get('issues', [])[:2])
        if decision == ModerationDecision.Decision.APPROVE:
            reasons = ['Propuesta de aprobación sujeta a revisión humana. La evaluación recomienda '
                       f'«{recommended.label}»; justifica aquí por qué procede aprobar.'] + findings
            directions = ['Antes de aprobar, resuelve y documenta las incidencias y verificaciones '
                          'pendientes de la evaluación. Confirma el estado, los datos y las '
                          'fotografías del artículo real.']
        elif decision == ModerationDecision.Decision.REJECT:
            reasons = ['Propuesta de rechazo sujeta a revisión humana. La evaluación recomienda '
                       f'«{recommended.label}»; concreta aquí el motivo que justifica el rechazo.'] + findings
            directions = ['Explica a la persona el motivo del rechazo y si puede presentar una '
                          'nueva publicación con las correcciones necesarias.']
        else:
            reasons = ['Solicitud de cambios por concretar. La evaluación recomienda '
                       f'«{recommended.label}»; indica qué cambios necesitas y por qué.'] + findings
            if recommended == ModerationDecision.Decision.REJECT:
                directions = ['Aclara con el personal la admisión del artículo antes de volver a '
                              'enviarlo. Pedir cambios no resuelve por sí solo la incidencia '
                              'sobre artículos no admitidos.']
            else:
                directions = ['Detalla la información o las fotografías que debe corregir la '
                              'persona antes de volver a enviar la publicación.']

    reason = _bounded_text(reasons, 900)
    indications = _bounded_text(directions, 1000)
    proposal = {
        'decision': decision.value, 'label': decision.label,
        'reason': reason, 'indications': indications,
        'notes': f'Motivo:\n{reason}\n\nIndicaciones:\n{indications}',
        'evaluation_id': evaluation.pk, 'is_simulated': evaluation.is_simulated,
    }
    if choice is None:
        proposal['drafts'] = {
            option.value: decision_proposal(evaluation, choice=option)['notes']
            for option in ModerationDecision.Decision
        }
    return proposal
