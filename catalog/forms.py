from django import forms

from catalog.models import Category, public_category_ids
from locations.models import RecyclingCenter


class HierarchicalCategoryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, category):
        labels = [category.name]
        parent = category.parent
        visited = {category.pk}
        while parent is not None and parent.pk not in visited:
            labels.append(parent.name)
            visited.add(parent.pk)
            parent = parent.parent
        return ' / '.join(reversed(labels))


class CatalogFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=120,
        label='Buscar',
        widget=forms.TextInput(
            attrs={
                'type': 'search',
                'placeholder': 'Buscar objetos',
                'autocomplete': 'off',
            }
        ),
    )
    category = HierarchicalCategoryChoiceField(
        required=False,
        queryset=Category.objects.none(),
        to_field_name='slug',
        empty_label='Todas las categorías',
        label='Categoría',
    )
    subcategory = HierarchicalCategoryChoiceField(
        required=False,
        queryset=Category.objects.none(),
        to_field_name='slug',
        empty_label='Todas las subcategorías',
        label='Subcategoría',
    )
    center = forms.ModelChoiceField(
        required=False,
        queryset=RecyclingCenter.objects.none(),
        to_field_name='slug',
        empty_label='Todos los puntos limpios',
        label='Punto Limpio',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        public_categories = Category.objects.filter(
            pk__in=public_category_ids(),
        ).select_related('parent')
        self.fields['category'].queryset = public_categories.filter(parent__isnull=True)

        subcategories = public_categories.filter(parent__isnull=False)
        selected_category = self.data.get('category') if self.is_bound else None
        if selected_category:
            category = self.fields['category'].queryset.filter(slug=selected_category).first()
            if category:
                descendant_ids = set()
                pending_ids = [category.pk]
                while pending_ids:
                    child_ids = list(
                        public_categories.filter(parent_id__in=pending_ids).values_list(
                            'pk', flat=True
                        )
                    )
                    descendant_ids.update(child_ids)
                    pending_ids = child_ids
                subcategories = subcategories.filter(pk__in=descendant_ids)
        self.fields['subcategory'].queryset = subcategories
        self.fields['center'].queryset = RecyclingCenter.objects.filter(
            is_active=True,
            operational_status=RecyclingCenter.OperationalStatus.OPEN,
        )

    def clean_q(self):
        return ' '.join(self.cleaned_data['q'].split())

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        subcategory = cleaned_data.get('subcategory')
        if category and subcategory:
            ancestor = subcategory.parent
            while ancestor is not None and ancestor != category:
                ancestor = ancestor.parent
            if ancestor is None:
                self.add_error(
                    'subcategory',
                    'La subcategoría no pertenece a la categoría seleccionada.',
                )
        return cleaned_data
