from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve


class ForcePasswordChangeMiddleware:
    exempt_url_names = {'password-change', 'logout'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        must_change_local_password = (
            user
            and user.is_authenticated
            and user.must_change_password
            and settings.AUTH_PROVIDER == 'local'
            and user.auth_source == 'local'
        )
        if must_change_local_password:
            static_prefix = f'/{settings.STATIC_URL.lstrip("/")}'
            media_prefix = f'/{settings.MEDIA_URL.lstrip("/")}'
            if not request.path_info.startswith((static_prefix, media_prefix)):
                try:
                    match = resolve(request.path_info)
                except Resolver404:
                    match = None
                if match is None or match.url_name not in self.exempt_url_names:
                    return redirect('password-change')
        return self.get_response(request)
