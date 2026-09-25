from django.db import connection
from django.db.models import BooleanField, Exists, OuterRef, Value
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from inventory.models import InventoryItem
from catalog.models import Favorite


def home(request):
    featured_objects = (
        InventoryItem.objects.public_catalog()
        .select_related('center', 'category_node', 'publication')
        .with_public_photos()
    )
    if request.user.is_authenticated:
        featured_objects = featured_objects.annotate(
            is_favorite=Exists(
                Favorite.objects.filter(
                    user=request.user,
                    reusable_object_id=OuterRef('pk'),
                )
            )
        )
    else:
        featured_objects = featured_objects.annotate(
            is_favorite=Value(False, output_field=BooleanField())
        )
    featured_objects = featured_objects.order_by('-created_at')[:4]

    if request.user.is_authenticated:
        publish_url = reverse('publications:create')
    else:
        publish_url = reverse('register')

    start_url = reverse('profile') if request.user.is_authenticated else reverse('register')

    return render(
        request,
        'public/home.html',
        {
            'featured_objects': featured_objects,
            'publish_url': publish_url,
            'start_url': start_url,
        },
    )


def legal_notice(request):
    return render(
        request,
        'public/legal/notice.html',
        {
            'page_title': 'Aviso legal',
            'page_summary': 'Condiciones generales de acceso y uso de la plataforma TRUEC@.',
            'source_url': 'https://www.tenetrueca.com/aviso-legal',
        },
    )


def cookie_policy(request):
    return render(
        request,
        'public/legal/cookies.html',
        {
            'page_title': 'Política de cookies',
            'page_summary': 'Información sobre las cookies utilizadas y cómo gestionar tus preferencias.',
            'source_url': 'https://www.tenetrueca.com/politica-cookies',
        },
    )


def privacy_policy(request):
    return render(
        request,
        'public/legal/privacy.html',
        {
            'page_title': 'Política de privacidad',
            'page_summary': 'Cómo se tratan y protegen los datos personales en TRUEC@.',
            'source_url': 'https://www.tenetrueca.com/politica-privacidad',
        },
    )


def health(request):
    database_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        database_ok = False

    status_code = 200 if database_ok else 503
    return JsonResponse(
        {
            'status': 'ok' if database_ok else 'degraded',
            'checks': {
                'app': 'ok',
                'database': 'ok' if database_ok else 'error',
            },
        },
        status=status_code,
    )
