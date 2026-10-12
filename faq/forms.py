from django import forms

from accounts.forms import AccessibleFieldsMixin
from faq.models import FAQ


class FAQForm(AccessibleFieldsMixin, forms.ModelForm):
    class Meta:
        model = FAQ
        fields = ('question', 'answer', 'position', 'is_published')
        widgets = {
            'answer': forms.Textarea(attrs={'rows': 8}),
            'position': forms.NumberInput(attrs={'min': 0}),
        }
        help_texts = {
            'answer': 'Escribe texto sin HTML. Se conservarán los saltos de línea.',
            'position': 'Las posiciones menores aparecen primero; se admite el valor 0.',
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


class FAQDeleteForm(AccessibleFieldsMixin, forms.Form):
    confirm = forms.BooleanField(label='Confirmo que quiero eliminar esta pregunta.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark_field_errors()
