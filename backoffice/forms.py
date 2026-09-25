from django import forms
from django.db import models

from accounts.forms import AccessibleFieldsMixin
from backoffice.models import StaffInvitation
from locations.models import RecyclingCenter


class StaffInviteForm(AccessibleFieldsMixin, forms.Form):
    class ProvisioningMethod(models.TextChoices):
        INVITATION = 'invitation', 'Enviar invitación por email'
        TEMPORARY_PASSWORD = 'temporary_password', 'Crear con contraseña temporal'

    first_name = forms.CharField(
        label='Nombre',
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'given-name'}),
    )
    last_name = forms.CharField(
        label='Apellidos',
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}),
    )
    email = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )
    role = forms.ChoiceField(
        label='Rol',
        choices=(('', 'Selecciona un rol'), *StaffInvitation.Role.choices),
    )
    center = forms.ModelChoiceField(
        label='Punto limpio',
        queryset=RecyclingCenter.objects.filter(is_active=True),
        required=False,
        empty_label='Sin asignación',
        help_text='Opcional. Cada gestor puede trabajar en un único punto limpio.',
    )
    provisioning_method = forms.ChoiceField(
        label='Método de acceso',
        choices=ProvisioningMethod.choices,
        initial=ProvisioningMethod.INVITATION,
        required=False,
        widget=forms.RadioSelect,
        help_text=(
            'La invitación permite que la persona cree su propia contraseña. '
            'La contraseña temporal se mostrará una sola vez.'
        ),
    )

    def __init__(self, *args, local_auth_enabled=True, **kwargs):
        self.local_auth_enabled = local_auth_enabled
        super().__init__(*args, **kwargs)
        if not local_auth_enabled:
            self.fields['provisioning_method'].choices = [
                (
                    self.ProvisioningMethod.INVITATION,
                    self.ProvisioningMethod.INVITATION.label,
                )
            ]
            self.fields['provisioning_method'].widget = forms.HiddenInput()
        self.mark_field_errors()

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def clean_provisioning_method(self):
        method = (
            self.cleaned_data.get('provisioning_method')
            or self.ProvisioningMethod.INVITATION
        )
        if (
            method == self.ProvisioningMethod.TEMPORARY_PASSWORD
            and not self.local_auth_enabled
        ):
            raise forms.ValidationError(
                'La contraseña temporal solo está disponible con autenticación local.'
            )
        return method

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('role') != StaffInvitation.Role.MANAGER:
            cleaned_data['center'] = None
        return cleaned_data


class StaffMemberForm(AccessibleFieldsMixin, forms.Form):
    first_name = forms.CharField(
        label='Nombre',
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'given-name'}),
    )
    last_name = forms.CharField(
        label='Apellidos',
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}),
    )
    email = forms.EmailField(label='Correo electrónico', disabled=True)
    role = forms.ChoiceField(label='Rol', choices=StaffInvitation.Role.choices)
    center = forms.ModelChoiceField(
        label='Punto limpio',
        queryset=RecyclingCenter.objects.filter(is_active=True),
        required=False,
        empty_label='Sin asignación',
        help_text='El cambio de centro se aplica en la siguiente petición del gestor.',
    )

    def __init__(
        self,
        *args,
        user,
        initial_role,
        allow_role_change=True,
        **kwargs,
    ):
        self.user = user
        initial = kwargs.setdefault('initial', {})
        initial.update(
            {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
                'role': initial_role,
                'center': (
                    user.center_accesses.filter(is_active=True)
                    .values_list('center_id', flat=True)
                    .first()
                ),
            }
        )
        super().__init__(*args, **kwargs)
        self.fields['role'].disabled = not allow_role_change
        self.mark_field_errors()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('role') != StaffInvitation.Role.MANAGER:
            cleaned_data['center'] = None
        return cleaned_data

    def save(self):
        self.user.first_name = self.cleaned_data['first_name'].strip()
        self.user.last_name = self.cleaned_data['last_name'].strip()
        self.user.save(update_fields=['first_name', 'last_name'])
        return self.user
