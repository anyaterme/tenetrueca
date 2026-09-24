from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render


def home(request):
    return render(request, 'public/home.html')


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
