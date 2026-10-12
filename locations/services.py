from django.conf import settings

from locations.models import RecyclingCenter


def maps_configuration():
    return {
        'apiKey': getattr(settings, 'GOOGLE_MAPS_API_KEY', ''),
        'mapId': getattr(settings, 'GOOGLE_MAPS_MAP_ID', ''),
    }


def public_centers():
    return RecyclingCenter.objects.filter(
        is_active=True, show_on_public_map=True,
        latitude__isnull=False, longitude__isnull=False,
    ).exclude(address='').order_by('name')


def center_map_data(centers):
    return [{
        'id': center.pk, 'name': center.name, 'address': center.address,
        'lat': float(center.latitude), 'lng': float(center.longitude),
        'phone': center.phone, 'schedule': center.schedule,
        'status': center.get_operational_status_display(),
        'information': center.public_information, 'directionsUrl': center.directions_url,
    } for center in centers]
