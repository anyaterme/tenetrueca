"""
URL configuration for tenetrueca project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

from core.views import cookie_policy, health, home, legal_notice, privacy_policy

urlpatterns = [
    path('', home, name='home'),
    path('aviso-legal/', legal_notice, name='legal_notice'),
    path('politica-cookies/', cookie_policy, name='cookie_policy'),
    path('politica-privacidad/', privacy_policy, name='privacy_policy'),
    path('cuenta/', include('accounts.urls')),
    path('backoffice/', include('backoffice.urls')),
    path('catalogo/', include('catalog.urls')),
    path('publicaciones/', include('publications.citizen_urls')),
    path('reservas/', include('reservations.urls')),
    path('cuenta/puntos/', include('points.urls')),
    path('moderacion/', include('moderation.urls')),
    path('recepcion/', include('inventory.urls')),
    path('fotografias/', include('publications.urls')),
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),
    path('api/v1/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
