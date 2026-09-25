from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils import timezone

from accounts.models import User
from locations.models import RecyclingCenter


class AccessibleFieldsMixin:
    def mark_field_errors(self):
        for field_name, field in self.fields.items():
            field.widget.attrs['aria-errormessage'] = f'id_{field_name}-errors'
            if self.is_bound and self[field_name].errors:
                field.widget.attrs['aria-invalid'] = 'true'


class EmailAuthenticationForm(AccessibleFieldsMixin, AuthenticationForm):
    username = forms.EmailField(
        label='Correo electrónico',
        widget=forms.EmailInput(
            attrs={
                'autocomplete': 'email',
                'autofocus': True,
                'placeholder': 'ej. nombre@tudominio.com',
            }
        ),
    )
    password = forms.CharField(
        label='Contraseña',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'autocomplete': 'current-password',
                'placeholder': 'Introduce tu contraseña',
            }
        ),
    )
    error_messages = {
        'invalid_login': 'Correo electrónico o contraseña incorrectos.',
        'inactive': 'Correo electrónico o contraseña incorrectos.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark_field_errors()


class MagicLinkRequestForm(AccessibleFieldsMixin, forms.Form):
    identifier = forms.CharField(
        label='Correo electrónico o nombre de usuario',
        max_length=254,
        widget=forms.TextInput(
            attrs={
                'autocomplete': 'username',
                'autofocus': True,
                'placeholder': 'tu correo o nombre de usuario',
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark_field_errors()

    def clean_identifier(self):
        return self.cleaned_data['identifier'].strip()


class RegistrationForm(AccessibleFieldsMixin, UserCreationForm):
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
    username = forms.CharField(
        label='Nombre de usuario',
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'username'}),
    )
    nif_nie = forms.CharField(
        label='NIF/NIE',
        max_length=32,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
    )
    accept_terms = forms.BooleanField(
        label='Acepto la política de privacidad y los términos de uso',
        required=True,
        error_messages={'required': 'Debes aceptar la política de privacidad y los términos de uso.'},
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('first_name', 'last_name', 'username', 'email', 'nif_nie')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'
        self.mark_field_errors()

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con este correo electrónico.')
        return email

    def clean_username(self):
        username = self.cleaned_data['username'].strip().lower()
        if '@' in username:
            raise forms.ValidationError('El nombre de usuario no puede contener @.')
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Ya existe una cuenta con este nombre de usuario.')
        return username

    def clean_nif_nie(self):
        return self.cleaned_data['nif_nie'].strip().upper()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.account_status = User.AccountStatus.ACTIVE
        user.auth_source = User.AuthSource.LOCAL
        user.consent_version = settings.TERMS_CONSENT_VERSION
        user.consent_accepted_at = timezone.now()
        if commit:
            user.save()
        return user


class ProfileUpdateForm(AccessibleFieldsMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = (
            'first_name',
            'last_name',
            'nif_nie',
            'phone',
            'habitual_recycling_center',
        )
        labels = {
            'first_name': 'Nombre',
            'last_name': 'Apellidos',
            'nif_nie': 'NIF/NIE',
            'phone': 'Teléfono',
            'habitual_recycling_center': 'Punto limpio habitual',
        }
        widgets = {
            'first_name': forms.TextInput(attrs={'autocomplete': 'given-name'}),
            'last_name': forms.TextInput(attrs={'autocomplete': 'family-name'}),
            'nif_nie': forms.TextInput(attrs={'autocomplete': 'off'}),
            'phone': forms.TextInput(attrs={'autocomplete': 'tel'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nif_is_locked = bool(self.instance.pk and self.instance.nif_nie.strip())
        if self.nif_is_locked:
            self.fields.pop('nif_nie')
        else:
            self.fields['nif_nie'].required = False
        self.fields['habitual_recycling_center'].queryset = RecyclingCenter.objects.filter(
            is_active=True,
            operational_status=RecyclingCenter.OperationalStatus.OPEN,
        )
        self.fields['habitual_recycling_center'].required = False
        self.mark_field_errors()

    def clean_nif_nie(self):
        return self.cleaned_data['nif_nie'].strip().upper()


class PreferencesForm(AccessibleFieldsMixin, forms.Form):
    optional_email_communications = forms.BooleanField(
        label='Quiero recibir comunicaciones opcionales por correo electrónico',
        required=False,
        help_text='Las notificaciones de seguridad, reservas y operaciones seguirán activas.',
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        initial = kwargs.setdefault('initial', {})
        initial.setdefault(
            'optional_email_communications',
            user.preferences.get('optional_email_communications', False),
        )
        super().__init__(*args, **kwargs)
        self.mark_field_errors()

    def save(self):
        preferences = dict(self.user.preferences)
        preferences['optional_email_communications'] = self.cleaned_data[
            'optional_email_communications'
        ]
        self.user.preferences = preferences
        self.user.save(update_fields=['preferences'])
        return self.user
