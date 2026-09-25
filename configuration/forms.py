from email.utils import parseaddr

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from accounts.forms import AccessibleFieldsMixin
from configuration.models import EmailConfiguration


class EmailConfigurationForm(AccessibleFieldsMixin, forms.Form):
    PASSWORD_KEEP = 'keep'
    PASSWORD_REPLACE = 'replace'
    PASSWORD_INHERIT = 'inherit'

    email_host = forms.CharField(
        label='Servidor SMTP',
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'smtp.example.com'}),
    )
    override_email_host = forms.BooleanField(label='Personalizar', required=False)
    email_port = forms.IntegerField(
        label='Puerto',
        min_value=1,
        max_value=65535,
        required=False,
        widget=forms.NumberInput(attrs={'inputmode': 'numeric'}),
    )
    override_email_port = forms.BooleanField(label='Personalizar', required=False)
    security = forms.ChoiceField(
        label='Seguridad',
        choices=EmailConfiguration.Security.choices,
        required=False,
        widget=forms.RadioSelect,
    )
    override_security = forms.BooleanField(label='Personalizar', required=False)
    email_host_user = forms.CharField(
        label='Usuario SMTP',
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
    )
    override_email_host_user = forms.BooleanField(label='Personalizar', required=False)
    default_from_email = forms.CharField(
        label='Remitente predeterminado',
        max_length=320,
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'email'}),
    )
    override_default_from_email = forms.BooleanField(label='Personalizar', required=False)
    email_timeout = forms.IntegerField(
        label='Timeout',
        min_value=1,
        max_value=300,
        required=False,
        widget=forms.NumberInput(attrs={'inputmode': 'numeric'}),
    )
    override_email_timeout = forms.BooleanField(label='Personalizar', required=False)
    password_action = forms.ChoiceField(
        label='Contraseña SMTP',
        choices=(
            (PASSWORD_KEEP, 'Mantener credencial actual'),
            (PASSWORD_REPLACE, 'Guardar una nueva credencial'),
            (PASSWORD_INHERIT, 'Usar credencial del servidor'),
        ),
        widget=forms.RadioSelect,
    )
    new_password = forms.CharField(
        label='Nueva contraseña SMTP',
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password', 'placeholder': 'Nueva contraseña'}
        ),
    )

    override_fields = (
        'email_host',
        'email_port',
        'security',
        'email_host_user',
        'default_from_email',
        'email_timeout',
    )
    required_override_fields = {
        'email_host',
        'email_port',
        'security',
        'default_from_email',
        'email_timeout',
    }

    def __init__(self, *args, override=None, effective=None, **kwargs):
        self.override = override
        self.effective = effective
        if not args and not kwargs.get('data') and effective is not None:
            initial = kwargs.setdefault('initial', {})
            initial.update(
                {
                    'email_host': effective.host,
                    'email_port': effective.port,
                    'security': effective.security,
                    'email_host_user': effective.username,
                    'default_from_email': effective.default_from_email,
                    'email_timeout': effective.timeout,
                    'password_action': (
                        self.PASSWORD_KEEP
                        if override and override.email_host_password_encrypted is not None
                        else self.PASSWORD_INHERIT
                    ),
                }
            )
            for field_name in self.override_fields:
                initial[f'override_{field_name}'] = bool(
                    override and getattr(override, field_name) is not None
                )
        super().__init__(*args, **kwargs)
        for field_name in self.override_fields:
            toggle_name = f'override_{field_name}'
            self.fields[toggle_name].widget.attrs['data-override-toggle'] = (
                f'id_{field_name}'
            )
            self.fields[field_name].widget.attrs['data-override-input'] = 'true'
            enabled = (
                bool(self.data.get(toggle_name))
                if self.is_bound
                else bool(self.initial.get(toggle_name))
            )
            if not enabled:
                self.fields[field_name].widget.attrs['disabled'] = True

        password_action = (
            self.data.get('password_action')
            if self.is_bound
            else self.initial.get('password_action')
        )
        if not (override and override.email_host_password_encrypted is not None):
            self.fields['password_action'].choices = (
                (self.PASSWORD_REPLACE, 'Guardar una nueva credencial'),
                (self.PASSWORD_INHERIT, 'Usar credencial del servidor'),
            )
        self.fields['new_password'].widget.attrs['data-password-replacement'] = 'true'
        if password_action != self.PASSWORD_REPLACE:
            self.fields['new_password'].widget.attrs['disabled'] = True
        self.mark_field_errors()

    def clean_default_from_email(self):
        value = self.cleaned_data.get('default_from_email', '').strip()
        if '\n' in value or '\r' in value:
            raise forms.ValidationError('Introduce un remitente válido.')
        if not value:
            return value
        _, address = parseaddr(value)
        try:
            validate_email(address)
        except ValidationError as error:
            raise forms.ValidationError('Introduce un remitente válido.') from error
        return value

    def clean(self):
        cleaned_data = super().clean()
        overrides = {}
        for field_name in self.override_fields:
            is_overridden = bool(cleaned_data.get(f'override_{field_name}'))
            field_value = cleaned_data.get(field_name)
            if is_overridden and field_name in self.required_override_fields:
                if field_value in (None, ''):
                    self.add_error(field_name, 'Este valor es obligatorio al personalizarlo.')
            overrides[field_name] = field_value if is_overridden else None

        password_action = cleaned_data.get('password_action')
        if password_action == self.PASSWORD_REPLACE and not cleaned_data.get('new_password'):
            self.add_error('new_password', 'Introduce la nueva contraseña SMTP.')

        final_host = (
            overrides['email_host']
            if overrides['email_host'] is not None
            else getattr(settings, 'EMAIL_HOST', '')
        )
        if str(getattr(settings, 'EMAIL_BACKEND', '')).endswith('smtp.EmailBackend'):
            if not final_host:
                self.add_error(
                    'email_host',
                    'El servidor SMTP es obligatorio para el backend configurado.',
                )

        final_sender = (
            overrides['default_from_email']
            if overrides['default_from_email'] is not None
            else getattr(settings, 'DEFAULT_FROM_EMAIL', '')
        )
        _, sender_address = parseaddr(str(final_sender))
        try:
            validate_email(sender_address)
        except ValidationError:
            self.add_error(
                'default_from_email',
                'El remitente efectivo no es una dirección válida.',
            )

        self.overrides = overrides
        return cleaned_data


class EmailTestForm(AccessibleFieldsMixin, forms.Form):
    recipient = forms.EmailField(
        label='Destinatario',
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark_field_errors()
