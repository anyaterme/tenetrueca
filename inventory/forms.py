from django import forms

from catalog.models import ReusableObject
from inventory.models import ReceptionInspection


class ReceptionQueueFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label='Buscar publicación',
        max_length=180,
        widget=forms.TextInput(attrs={'placeholder': 'Título, correo o nombre de usuario'}),
    )
    ordering = forms.ChoiceField(
        required=False,
        label='Orden',
        choices=(('oldest', 'Más antiguas primero'), ('newest', 'Más recientes primero')),
    )


class ReceptionInspectionForm(forms.Form):
    center = forms.ModelChoiceField(
        label='Punto Limpio receptor',
        queryset=None,
        empty_label='Selecciona un centro',
    )
    decision = forms.ChoiceField(
        label='Resultado de la inspección',
        choices=ReceptionInspection.Decision.choices,
        widget=forms.RadioSelect,
    )
    condition = forms.ChoiceField(
        label='Condición física',
        choices=ReusableObject.Condition.choices,
    )
    internal_location = forms.CharField(
        required=False,
        label='Ubicación interna',
        max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'Ej. Nave 2 · Estantería A-04'}),
    )
    notes = forms.CharField(
        required=False,
        label='Observaciones',
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                'rows': 5,
                'placeholder': 'Anota el resultado de la revisión física.',
            }
        ),
    )

    def __init__(self, *args, centers, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['center'].queryset = centers
        if centers.count() == 1:
            self.fields['center'].initial = centers.first()

    def clean(self):
        cleaned_data = super().clean()
        decision = cleaned_data.get('decision')
        notes = (cleaned_data.get('notes') or '').strip()
        if decision == ReceptionInspection.Decision.REJECT and not notes:
            self.add_error('notes', 'Indica el motivo del rechazo en la recepción.')
        cleaned_data['notes'] = notes
        return cleaned_data
