from audit.models import AuditEvent


FAQ_FIELDS = {
    'question': 'Pregunta', 'answer': 'Respuesta',
    'position': 'Posición', 'is_published': 'Publicación',
}
FAQ_ACTIONS = {'faq.created': 'Creación', 'faq.updated': 'Modificación', 'faq.deleted': 'Eliminación'}


def faq_snapshot(faq):
    return {field: getattr(faq, field) for field in FAQ_FIELDS}


def record_faq_event(*, actor, faq_id, action, before=None, after=None, source='backoffice'):
    if action == 'faq.updated' and before == after:
        return None
    return AuditEvent.objects.create(
        actor=actor, action=action, entity='FAQ', entity_id=str(faq_id),
        source=source, before=before, after=after,
        metadata={
            'actor_label': actor.get_full_name() or actor.username or f'Usuari@ #{actor.pk}',
            'actor_id': actor.pk,
        },
    )


def history_item(event):
    before, after = event.before or {}, event.after or {}

    def display(snapshot, field):
        if field not in snapshot:
            return '—'
        if field == 'is_published':
            return 'Publicada' if snapshot[field] else 'No publicada'
        return str(snapshot[field])

    return {
        'event': event,
        'action_label': FAQ_ACTIONS[event.action],
        'question': after.get('question') or before.get('question') or f'Pregunta #{event.entity_id}',
        'actor_label': event.metadata.get('actor_label') or (
            event.actor.get_full_name() or event.actor.username if event.actor else 'Cuenta eliminada'
        ),
        'changes': [
            {'label': label, 'before': display(before, field), 'after': display(after, field)}
            for field, label in FAQ_FIELDS.items()
            if before.get(field) != after.get(field)
        ],
    }
