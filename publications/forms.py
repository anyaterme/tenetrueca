from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

from catalog.models import Category
from publications.images import normalize_publication_photo
from publications.models import Publication, PublicationPhoto


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiplePublicationPhotoField(forms.FileField):
    widget = MultipleFileInput(
        attrs={
            'accept': '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp',
        }
    )

    def clean(self, data, initial=None):
        uploads = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        normalized_photos = []
        errors = []
        for upload in uploads:
            try:
                normalized = normalize_publication_photo(upload)
                normalized.content._normalized_publication_photo = normalized
                normalized_photos.append(normalized.content)
            except ValidationError as error:
                errors.extend(error.error_list)
        if errors:
            raise ValidationError(errors)
        return normalized_photos


class HierarchicalPublicationCategoryField(forms.ModelChoiceField):
    def label_from_instance(self, category):
        labels = [category.name]
        parent = category.parent
        visited = {category.pk}
        while parent is not None and parent.pk not in visited:
            labels.append(parent.name)
            visited.add(parent.pk)
            parent = parent.parent
        return ' / '.join(reversed(labels))


class PublicationForm(forms.ModelForm):
    class Meta:
        model = Publication
        fields = ('title', 'description', 'category', 'tags')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = Category.objects.filter(is_active=True)
        if self.instance.pk and self.instance.category_id:
            categories = Category.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.category_id)
            )
        self.fields['category'].queryset = categories


class PublicationAdminForm(PublicationForm):
    class Meta:
        model = Publication
        fields = '__all__'


class PublicationPhotoForm(forms.ModelForm):
    class Meta:
        model = PublicationPhoto
        fields = ('image', 'alt_text', 'sort_order', 'is_primary')

    def clean_image(self):
        image = self.cleaned_data['image']
        if self.instance.pk and image == self.instance.image:
            return image
        normalized = normalize_publication_photo(image)
        normalized.content._normalized_publication_photo = normalized
        return normalized.content


class CitizenPublicationForm(forms.ModelForm):
    parent_category = forms.ModelChoiceField(
        queryset=Category.objects.none(),
        label='Categoría',
        empty_label='Selecciona una categoría',
    )
    category = HierarchicalPublicationCategoryField(
        queryset=Category.objects.none(),
        required=False,
        label='Subcategoría',
        empty_label='Selecciona una subcategoría',
    )
    photos = MultiplePublicationPhotoField(
        required=False,
        label='Fotografías',
        help_text=(
            f'Puedes añadir hasta {settings.PUBLICATION_PHOTO_MAX_COUNT} imágenes JPEG, PNG o WebP. '
            f'Máximo {settings.PUBLICATION_PHOTO_MAX_BYTES // (1024 * 1024)} MB por fotografía.'
        ),
    )
    remove_photos = forms.ModelMultipleChoiceField(
        queryset=PublicationPhoto.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Eliminar fotografías',
    )

    class Meta:
        model = Publication
        fields = ('title', 'description', 'category')
        labels = {
            'title': 'Nombre del objeto',
            'description': 'Descripción',
        }
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Ej. Lámpara de sobremesa'}),
            'description': forms.Textarea(
                attrs={
                    'rows': 6,
                    'placeholder': 'Describe el estado, características y cualquier detalle útil.',
                }
            ),
        }

    def __init__(self, *args, require_photo=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_photo = require_photo
        roots = Category.objects.filter(is_active=True, parent__isnull=True)
        active_categories = Category.objects.filter(is_active=True).select_related('parent')
        self.fields['parent_category'].queryset = roots
        self.fields['remove_photos'].queryset = (
            self.instance.photos.all() if self.instance.pk else PublicationPhoto.objects.none()
        )

        selected_root = None
        root_value = self.data.get('parent_category') if self.is_bound else None
        if root_value:
            selected_root = roots.filter(pk=root_value).first()
        elif self.instance.pk and self.instance.category_id:
            selected_root = self.instance.category
            visited = set()
            while selected_root.parent_id and selected_root.pk not in visited:
                visited.add(selected_root.pk)
                selected_root = selected_root.parent
            self.initial['parent_category'] = selected_root
            if self.instance.category_id != selected_root.pk:
                self.initial['category'] = self.instance.category

        subcategories = active_categories.filter(parent__isnull=False)
        if selected_root:
            descendant_ids = set()
            pending_ids = [selected_root.pk]
            while pending_ids:
                child_ids = list(
                    active_categories.filter(parent_id__in=pending_ids).values_list('pk', flat=True)
                )
                descendant_ids.update(child_ids)
                pending_ids = child_ids
            subcategories = subcategories.filter(pk__in=descendant_ids)
        self.fields['category'].queryset = subcategories
        self.order_fields(
            ('title', 'description', 'parent_category', 'category', 'photos', 'remove_photos')
        )

    def clean(self):
        cleaned_data = super().clean()
        parent_category = cleaned_data.get('parent_category')
        category = cleaned_data.get('category')

        if parent_category:
            has_subcategories = Category.objects.filter(
                parent=parent_category,
                is_active=True,
            ).exists()
            if has_subcategories and category is None:
                self.add_error('category', 'Selecciona una subcategoría.')
            elif category is not None:
                ancestor = category.parent
                while ancestor is not None and ancestor != parent_category:
                    ancestor = ancestor.parent
                if ancestor is None:
                    self.add_error(
                        'category',
                        'La subcategoría no pertenece a la categoría seleccionada.',
                    )

        final_category = category or parent_category
        if final_category and not final_category.is_available_for_publication():
            self.add_error('category', 'Selecciona una categoría activa.')
        cleaned_data['category'] = final_category

        new_photos = cleaned_data.get('photos') or []
        removed_photos = cleaned_data.get('remove_photos')
        existing_count = self.instance.photos.count() if self.instance.pk else 0
        removed_count = removed_photos.count() if removed_photos is not None else 0
        total_photos = existing_count - removed_count + len(new_photos)
        if total_photos > settings.PUBLICATION_PHOTO_MAX_COUNT:
            self.add_error(
                'photos',
                f'Cada publicación admite un máximo de {settings.PUBLICATION_PHOTO_MAX_COUNT} fotografías.',
            )
        if self.require_photo and total_photos == 0:
            self.add_error('photos', 'Añade al menos una fotografía antes de enviar a revisión.')
        return cleaned_data


class PublicationStatusFilterForm(forms.Form):
    status = forms.ChoiceField(
        required=False,
        label='Estado',
        choices=(('', 'Todos los estados'), *Publication.Status.choices),
    )


class PublicationWithdrawalForm(forms.Form):
    reason = forms.CharField(
        label='Motivo de retirada',
        min_length=3,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                'rows': 4,
                'placeholder': 'Indica brevemente por qué retiras esta publicación.',
            }
        ),
    )
