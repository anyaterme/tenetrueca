from django import forms

from locations.models import RecyclingCenter
from core.permissions import operational_scope
from moderation.models import ModerationDecision
from publications.models import Publication


class ModerationQueueFilterForm(forms.Form):
    status = forms.ChoiceField(
        required=False,
        label='Estado',
        choices=(
            ('', 'Todos los estados revisables'),
            (Publication.Status.PENDING_REVIEW, Publication.Status.PENDING_REVIEW.label),
            (Publication.Status.CHANGES_REQUESTED, Publication.Status.CHANGES_REQUESTED.label),
            (Publication.Status.APPROVED, Publication.Status.APPROVED.label),
            (Publication.Status.REJECTED, Publication.Status.REJECTED.label),
        ),
    )
    center = forms.ModelChoiceField(
        required=False,
        label='Punto Limpio habitual',
        queryset=RecyclingCenter.objects.filter(is_active=True),
        empty_label='Todos los centros',
        to_field_name='slug',
    )
    date_from = forms.DateField(
        required=False,
        label='Desde',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    date_to = forms.DateField(
        required=False,
        label='Hasta',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    ordering = forms.ChoiceField(
        required=False,
        label='Orden',
        choices=(('oldest', 'Más antiguas primero'), ('newest', 'Más recientes primero')),
    )

    def __init__(self, *args, user=None, center_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        scope = operational_scope(user) if user is not None else None
        if scope and scope.is_manager:
            center_ids = [scope.center.pk] if scope.center is not None else []
            self.fields['center'].queryset = self.fields['center'].queryset.filter(
                pk__in=center_ids
            )
            self.fields['center'].empty_label = None
            if scope.center is not None:
                self.fields['center'].initial = scope.center
        elif center_ids is not None:
            self.fields['center'].queryset = self.fields['center'].queryset.filter(
                pk__in=center_ids
            )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get('date_from')
        date_to = cleaned_data.get('date_to')
        if date_from and date_to and date_from > date_to:
            self.add_error('date_to', 'La fecha final debe ser posterior a la inicial.')
        return cleaned_data


class ModerationDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        label='Decisión',
        choices=ModerationDecision.Decision.choices,
        widget=forms.RadioSelect,
    )
    notes = forms.CharField(
        required=False,
        label='Motivo e indicaciones',
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                'rows': 5,
                'placeholder': 'Explica los cambios necesarios o el motivo del rechazo.',
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        decision = cleaned_data.get('decision')
        notes = (cleaned_data.get('notes') or '').strip()
        if decision in {
            ModerationDecision.Decision.REQUEST_CHANGES,
            ModerationDecision.Decision.REJECT,
        } and not notes:
            self.add_error('notes', 'Indica un motivo para esta decisión.')
        cleaned_data['notes'] = notes
        return cleaned_data
