from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from core.permissions import user_is_administrator
from locations.models import RecyclingCenter


ADMIN_CENTER_FILTER_SESSION_KEY = 'backoffice_admin_center_ids'


@dataclass(frozen=True)
class AdminCenterFilter:
    is_administrator: bool
    available_centers: tuple
    centers: tuple

    @property
    def is_all(self):
        return not self.centers

    @property
    def is_active(self):
        return self.is_administrator and not self.is_all

    @property
    def center_ids(self):
        return tuple(center.pk for center in self.centers)

    @property
    def label(self):
        if self.is_all:
            return 'Todos los centros'
        if len(self.centers) == 1:
            return self.centers[0].name
        return f'{len(self.centers)} centros seleccionados'

    @property
    def selection_label(self):
        count = len(self.centers)
        return f'{count} centro seleccionado' if count == 1 else f'{count} centros seleccionados'

    @property
    def indicator_label(self):
        if not self.is_active:
            return ''
        if len(self.centers) <= 3:
            names = [center.name.removeprefix('Punto Limpio de ') for center in self.centers]
            if len(names) == 1:
                return names[0]
            return f'{", ".join(names[:-1])} y {names[-1]}'
        return f'{len(self.centers)} puntos limpios seleccionados'


def get_admin_center_filter(request):
    cached = getattr(request, '_admin_center_filter', None)
    if cached is not None:
        return cached

    if not user_is_administrator(request.user):
        center_filter = AdminCenterFilter(False, (), ())
    else:
        available = tuple(RecyclingCenter.objects.filter(is_active=True).order_by('name', 'pk'))
        stored_ids = request.session.get(ADMIN_CENTER_FILTER_SESSION_KEY)
        selected_ids = set(stored_ids) if isinstance(stored_ids, list) else set()
        selected = tuple(center for center in available if center.pk in selected_ids)
        center_filter = AdminCenterFilter(True, available, selected)

    request._admin_center_filter = center_filter
    return center_filter


def set_admin_center_filter(request, center_ids):
    if not user_is_administrator(request.user):
        raise PermissionDenied
    try:
        requested_ids = {int(center_id) for center_id in center_ids}
    except (TypeError, ValueError) as error:
        raise ValidationError('La selección contiene centros no válidos.') from error
    if not requested_ids:
        raise ValidationError('Selecciona al menos un punto limpio.')

    valid_ids = set(
        RecyclingCenter.objects.filter(is_active=True, pk__in=requested_ids).values_list(
            'pk', flat=True
        )
    )
    if valid_ids != requested_ids:
        raise ValidationError('La selección contiene centros no válidos.')

    request.session[ADMIN_CENTER_FILTER_SESSION_KEY] = sorted(valid_ids)
    if hasattr(request, '_admin_center_filter'):
        del request._admin_center_filter


def clear_admin_center_filter(request):
    if not user_is_administrator(request.user):
        raise PermissionDenied
    request.session.pop(ADMIN_CENTER_FILTER_SESSION_KEY, None)
    if hasattr(request, '_admin_center_filter'):
        del request._admin_center_filter


def filter_queryset_by_admin_centers(request, queryset, *, center_lookup):
    center_filter = get_admin_center_filter(request)
    if not center_filter.is_active:
        return queryset
    return queryset.filter(**{f'{center_lookup}__in': center_filter.center_ids})


def filter_publications_by_admin_centers(request, queryset):
    center_filter = get_admin_center_filter(request)
    if not center_filter.is_active:
        return queryset
    return queryset.filter(
        Q(inventory_object__center_id__in=center_filter.center_ids)
        | Q(
            inventory_object__isnull=True,
            submitter__habitual_recycling_center_id__in=center_filter.center_ids,
        )
    )


def filter_reservations_by_admin_centers(request, queryset):
    return filter_queryset_by_admin_centers(
        request,
        queryset,
        center_lookup='inventory_item__center_id',
    )


def filter_audit_events_by_admin_centers(request, queryset):
    center_filter = get_admin_center_filter(request)
    if not center_filter.is_active:
        return queryset
    has_center = Q(metadata__has_key='center_id') | Q(after__has_key='center_id')
    selected_center = Q(metadata__center_id__in=center_filter.center_ids) | Q(
        after__center_id__in=center_filter.center_ids
    )
    return queryset.filter(~has_center | selected_center)


def filter_centers_by_admin_selection(request, queryset):
    center_filter = get_admin_center_filter(request)
    if not center_filter.is_active:
        return queryset
    return queryset.filter(pk__in=center_filter.center_ids)
