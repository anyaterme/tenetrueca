from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from points.services import points_balance


@login_required
def movement_list(request):
    movements = request.user.point_movements.select_related('operation')
    paginator = Paginator(movements, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(
        request,
        'points/movement_list.html',
        {
            'balance': points_balance(request.user),
            'page_obj': page_obj,
            'paginator': paginator,
        },
    )
