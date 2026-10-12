from django import forms

from accounts.forms import AccessibleFieldsMixin
from locations.models import RecyclingCenter


class RecyclingCenterForm(AccessibleFieldsMixin, forms.ModelForm):
    class Meta:
        model = RecyclingCenter
        fields = ('name', 'slug', 'address', 'latitude', 'longitude', 'phone', 'schedule', 'operational_status', 'public_information', 'show_on_public_map')
        labels = {
            'name': 'Nombre', 'slug': 'Identificador', 'address': 'Dirección',
            'latitude': 'Latitud', 'longitude': 'Longitud', 'phone': 'Teléfono público',
            'schedule': 'Horario', 'operational_status': 'Estado operativo',
            'public_information': 'Información pública',
        }
        help_texts = {
            'slug': 'Identificador único del centro. Conserva el existente al editarlo.',
            'latitude': 'Puedes escribir las coordenadas o seleccionar la ubicación en el mapa.',
            'show_on_public_map': 'Controla su visibilidad en la web. No cambia sus objetos, reservas ni accesos del personal.',
        }
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'schedule': forms.Textarea(attrs={'rows': 3}),
            'public_information': forms.Textarea(attrs={'rows': 3}),
            'latitude': forms.NumberInput(attrs={'step': '0.000001', 'min': -90, 'max': 90}),
            'longitude': forms.NumberInput(attrs={'step': '0.000001', 'min': -180, 'max': 180}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark_field_errors()
        for name, field in self.fields.items():
            descriptions = []
            if field.help_text:
                descriptions.append(f'id_{name}-help')
            if self.is_bound and self[name].errors:
                descriptions.append(f'id_{name}-errors')
            if descriptions:
                field.widget.attrs['aria-describedby'] = ' '.join(descriptions)
