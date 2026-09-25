from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from configuration.models import FunctionalRule
from publications.models import Publication


DAILY_PUBLICATION_LIMIT_KEY = 'limite-publicaciones-diarias'


def get_daily_publication_limit():
    now = timezone.now()
    rule = (
        FunctionalRule.objects.filter(
            key=DAILY_PUBLICATION_LIMIT_KEY,
            is_active=True,
            effective_from__lte=now,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
        .order_by('-version', '-effective_from')
        .first()
    )
    try:
        value = int(rule.value) if rule is not None else settings.PUBLICATION_DAILY_LIMIT
    except (TypeError, ValueError):
        value = settings.PUBLICATION_DAILY_LIMIT
    return max(value, 0)


def daily_submission_count(user, now=None):
    current_time = timezone.localtime(now or timezone.now())
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return Publication.objects.filter(
        submitter=user,
        submitted_at__gte=day_start,
        submitted_at__lt=day_end,
    ).count()


def user_can_submit_today(user, now=None):
    limit = get_daily_publication_limit()
    return daily_submission_count(user, now=now) < limit, limit
