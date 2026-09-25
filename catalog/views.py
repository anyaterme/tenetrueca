from collections import defaultdict

from django.conf import settings
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import render

from catalog.forms import CatalogFilterForm
from catalog.models import Category
from inventory.models import InventoryItem


def _public_inventory():
    return (
        InventoryItem.objects.public_catalog()
        .select_related('center', 'category_node', 'category_node__parent', 'publication')
        .with_public_photos()
    )


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


def catalog_list(request):
    filter_form = CatalogFilterForm(request.GET)
    objects = _public_inventory()
    has_filters = any(
        request.GET.get(key) for key in ('q', 'category', 'subcategory', 'center')
    )

    if filter_form.is_valid():
        query = filter_form.cleaned_data['q']
        category = filter_form.cleaned_data['category']
        subcategory = filter_form.cleaned_data['subcategory']
        center = filter_form.cleaned_data['center']
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
    else:
        objects = objects.none()

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
        },
    )


def catalog_detail(request, reference):
    try:
        inventory_object = _public_inventory().get(reference=reference)
    except InventoryItem.DoesNotExist:
        raise Http404 from None
    return render(
        request,
        'catalog/object_detail.html',
        {'object': inventory_object},
    )
