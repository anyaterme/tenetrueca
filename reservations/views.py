from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from inventory.models import InventoryItem
from points.services import InsufficientPoints
from reservations.models import Reservation
from reservations.services import (
    InvalidReservationToken,
    cancel_reservation,
    complete_pickup,
    create_reservation,
    qr_png_data_uri,
    register_pickup_attempt,
    reservation_qr_url,
)


def _reservation_qr_context(request, reservation):
    if reservation.status != Reservation.Status.ACTIVE or reservation.qr_invalidated_at is not None:
        return {}
    qr_url = reservation_qr_url(request, reservation)
    return {
        'qr_url': qr_url,
        'qr_data_uri': qr_png_data_uri(qr_url),
    }


@login_required
def create_reservation_view(request, reference):
    if request.method != 'POST':
        raise Http404

    item = get_object_or_404(InventoryItem.objects.only('pk', 'reference'), reference=reference)
    try:
        reservation = create_reservation(inventory_item_id=item.pk, user=request.user)
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
        return redirect('catalog:detail', reference=reference)
    messages.success(request, 'Reserva confirmada. Presenta el QR al recoger el objeto.')
    return redirect('reservations:detail', public_id=reservation.public_id)


@login_required
def reservation_list(request):
    reservations = (
        Reservation.objects.filter(user=request.user)
        .select_related('inventory_item', 'inventory_item__center')
        .order_by('-reserved_at')
    )
    reservation_cards = []
    for reservation in reservations:
        context = {'reservation': reservation}
        context.update(_reservation_qr_context(request, reservation))
        reservation_cards.append(context)
    return render(
        request,
        'reservations/list.html',
        {'reservation_cards': reservation_cards},
    )


@login_required
def reservation_detail(request, public_id):
    reservation = get_object_or_404(
        Reservation.objects.select_related('inventory_item', 'inventory_item__center', 'pickup_center'),
        public_id=public_id,
        user=request.user,
    )
    context = {'reservation': reservation}
    context.update(_reservation_qr_context(request, reservation))
    return render(request, 'reservations/detail.html', context)


@login_required
def cancel_reservation_view(request, public_id):
    if request.method != 'POST':
        raise Http404
    try:
        cancel_reservation(reservation_public_id=public_id, user=request.user)
    except PermissionDenied:
        raise
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
    else:
        messages.success(request, 'Reserva cancelada.')
    return redirect('reservations:detail', public_id=public_id)


@login_required
def pickup_scan(request, token):
    try:
        if request.method == 'POST':
            reservation, completed = complete_pickup(token=token, operator=request.user)
            if completed:
                messages.success(request, 'Recogida completada y objeto marcado como entregado.')
            else:
                messages.info(request, 'La recogida ya estaba completada.')
            return render(
                request,
                'reservations/pickup_scan.html',
                {
                    'reservation': reservation,
                    'token': token,
                    'staff_section': 'pickup',
                    'cancel_url': reverse('inventory:reception_queue'),
                },
            )
        reservation = register_pickup_attempt(token=token, operator=request.user)
    except PermissionDenied:
        raise
    except InsufficientPoints as error:
        reservation = register_pickup_attempt(token=token, operator=request.user)
        return render(
            request,
            'reservations/pickup_scan.html',
            {
                'reservation': reservation,
                'token': token,
                'pickup_error': error.messages[0],
                'staff_section': 'pickup',
                'cancel_url': reverse('inventory:reception_queue'),
            },
            status=409,
        )
    except InvalidReservationToken as error:
        return render(
            request,
            'reservations/pickup_invalid.html',
            {'error_message': error.messages[0], 'staff_section': 'pickup'},
            status=400,
        )
    except ValidationError as error:
        return render(
            request,
            'reservations/pickup_invalid.html',
            {'error_message': error.messages[0], 'staff_section': 'pickup'},
            status=400,
        )

    return render(
        request,
        'reservations/pickup_scan.html',
        {
            'reservation': reservation,
            'token': token,
            'staff_section': 'pickup',
            'cancel_url': reverse('inventory:reception_queue'),
        },
    )
