"""Ordered case IDs from a versioned dataset, bounded by server-side authorization."""
import re

from django.conf import settings

from .errors import EvaluationStopped


def _ids(value):
    if (not isinstance(value, (tuple, list)) or not value or len(value) > 90
            or any(not isinstance(key, str) or not re.fullmatch(r'E[0-9]{2}', key) for key in value)
            or len(set(value)) != len(value)):
        raise EvaluationStopped('case_selection_invalid')
    return tuple(value)


def select_cases(cases, options):
    requested = options.get('case_ids')
    approved = settings.CHATBOT_EVAL_CASE_IDS
    if approved:
        approved = _ids(approved)
        if requested is None:
            raise EvaluationStopped('case_selection_required')
    if requested is None:
        return cases
    requested = _ids(requested)
    known = {case['id']: case for case in cases}
    if any(key not in known for key in requested):
        raise EvaluationStopped('case_not_in_dataset')
    if approved and requested != approved:
        raise EvaluationStopped('case_selection_not_authorized')
    live = options.get('allow_live') and not options.get('dry_run')
    if live and not approved:
        raise EvaluationStopped('case_selection_not_authorized')
    modes = 2 if options['mode'] in ('both', 'faq_compare') else 1
    if options['max_requests'] > len(requested) * modes:
        raise EvaluationStopped('case_selection_request_limit')
    return [known[key] for key in requested]


def execution_pairs(cases, modes, *, scoped):
    # Scoped pilots group modes as authorized; retain the original full-pilot order.
    return ([(case, mode) for mode in modes for case in cases] if scoped else
            [(case, mode) for case in cases for mode in modes])
