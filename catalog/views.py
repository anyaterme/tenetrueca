from collections import defaultdict

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.db.models import BooleanField, Exists, OuterRef, Prefetch, Value
from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalog.forms import CatalogFilterForm
from catalog.models import Category, Favorite
from inventory.models import InventoryItem
from publications.models import PublicationPhoto


def _public_inventory(user=None):
    objects = (
        InventoryItem.objects.public_catalog()
        .select_related('center', 'category_node', 'category_node__parent', 'publication')
        .with_public_photos()
    )
    if user is not None and user.is_authenticated:
        return objects.annotate(
            is_favorite=Exists(
                Favorite.objects.filter(user=user, reusable_object_id=OuterRef('pk'))
            )
        )
    return objects.annotate(is_favorite=Value(False, output_field=BooleanField()))


def _category_tree_ids(category):
    children_by_parent = defaultdict(list)
    for category_id, parent_id in Category.objects.filter(is_active=True).values_list(
        'id', 'parent_id'
    ):
        children_by_parent[parent_id].append(category_id)

    selected_ids = []
    pending_ids = [category.pk]
    while pending_ids:
        category_id = pending_ids.pop()
        selected_ids.append(category_id)
        pending_ids.extend(children_by_parent.get(category_id, ()))
    return selected_ids


def _catalog_url_without(request, *parameter_names):
    query = request.GET.copy()
    query.pop('page', None)
    for parameter_name in parameter_names:
        query.pop(parameter_name, None)
    encoded_query = query.urlencode()
    catalog_url = reverse('catalog:list')
    return f'{catalog_url}?{encoded_query}' if encoded_query else catalog_url


def catalog_list(request):
    filter_form = CatalogFilterForm(request.GET)
    objects = _public_inventory(request.user)
    has_filters = any(
        request.GET.get(key) for key in ('q', 'category', 'subcategory', 'center')
    )

    active_filter_chips = []
    ordering = ''
    if filter_form.is_valid():
        query = filter_form.cleaned_data['q']
        category = filter_form.cleaned_data['category']
        subcategory = filter_form.cleaned_data['subcategory']
        center = filter_form.cleaned_data['center']
        ordering = filter_form.cleaned_data['ordering']
        if query:
            objects = objects.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(category_node__name__icontains=query)
                | Q(publication__tags__name__icontains=query)
            ).distinct()
        if subcategory:
            objects = objects.filter(category_node_id__in=_category_tree_ids(subcategory))
        elif category:
            objects = objects.filter(category_node_id__in=_category_tree_ids(category))
        if center:
            objects = objects.filter(center=center)

        if center:
            active_filter_chips.append(
                {
                    'key': 'center',
                    'label': center.name,
                    'remove_url': _catalog_url_without(request, 'center'),
                }
            )
        if category:
            active_filter_chips.append(
                {
                    'key': 'category',
                    'label': category.name,
                    'remove_url': _catalog_url_without(
                        request, 'category', 'subcategory'
                    ),
                }
            )
        if subcategory:
            active_filter_chips.append(
                {
                    'key': 'subcategory',
                    'label': subcategory.name,
                    'remove_url': _catalog_url_without(request, 'subcategory'),
                }
            )
    else:
        objects = objects.none()

    if ordering == 'oldest':
        objects = objects.order_by(
            F('publication__approved_at').asc(nulls_last=True),
            'created_at',
            'pk',
        )
    elif ordering == 'title':
        objects = objects.order_by(Lower('title'), 'pk')
    else:
        objects = objects.order_by(
            F('publication__approved_at').desc(nulls_last=True),
            '-created_at',
            '-pk',
        )

    from django.core.paginator import Paginator

    paginator = Paginator(objects, settings.CATALOG_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    preserved_query = request.GET.copy()
    preserved_query.pop('page', None)

    return render(
        request,
        'catalog/object_list.html',
        {
            'filter_form': filter_form,
            'page_obj': page_obj,
            'paginator': paginator,
            'page_range': paginator.get_elided_page_range(page_obj.number),
            'preserved_query': preserved_query.urlencode(),
            'has_filters': has_filters,
            'active_filter_chips': active_filter_chips,
            'active_filter_count': len(active_filter_chips),
        },
    )


def catalog_detail(request, reference):
    try:
        inventory_object = _public_inventory(request.user).get(reference=reference)
    except InventoryItem.DoesNotExist:
        raise Http404 from None
    return render(
        request,
        'catalog/object_detail.html',
        {
            'object': inventory_object,
            'is_own_object': request.user.is_authenticated
            and request.user.pk
            in {
                inventory_object.owner_id,
                inventory_object.publication.submitter_id,
            },
        },
    )


def _favorite_redirect(request, item):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    if InventoryItem.objects.public_catalog().filter(pk=item.pk).exists():
        return redirect(item.get_absolute_url())
    return redirect('catalog:favorites')


@login_required
@require_POST
def favorite_add(request, reference):
    item = get_object_or_404(InventoryItem.objects.public_catalog(), reference=reference)
    _, created = Favorite.objects.get_or_create(user=request.user, reusable_object=item)
    if created:
        messages.success(request, 'Objeto añadido a tus favoritos.')
    return _favorite_redirect(request, item)


@login_required
@require_POST
def favorite_remove(request, reference):
    item = get_object_or_404(InventoryItem, reference=reference)
    Favorite.objects.filter(user=request.user, reusable_object=item).delete()
    messages.success(request, 'Objeto eliminado de tus favoritos.')
    return _favorite_redirect(request, item)


@login_required
def favorite_list(request):
    favorites = (
        Favorite.objects.filter(user=request.user)
        .select_related(
            'reusable_object',
            'reusable_object__center',
            'reusable_object__category_node',
            'reusable_object__category_node__parent',
            'reusable_object__publication',
        )
        .prefetch_related(
            Prefetch(
                'reusable_object__publication__photos',
                queryset=PublicationPhoto.objects.order_by(
                    '-is_primary', 'sort_order', 'created_at'
                ),
                to_attr='favorite_photos',
            )
        )
    )
    available_ids = set(
        InventoryItem.objects.public_catalog()
        .filter(favorited_by__user=request.user)
        .values_list('pk', flat=True)
    )
    favorite_cards = []
    for favorite in favorites:
        item = favorite.reusable_object
        item.is_available = item.pk in available_ids
        item.is_favorite = True
        photos = getattr(item.publication, 'favorite_photos', ()) if item.publication_id else ()
        favorite_cards.append(
            {
                'object': item,
                'primary_photo': photos[0] if photos and item.is_available else None,
                'detail_url': (
                    reverse('catalog:detail', kwargs={'reference': item.reference})
                    if item.is_available
                    else ''
                ),
            }
        )
    return render(
        request,
        'catalog/favorite_list.html',
        {'favorite_cards': favorite_cards},
    )
