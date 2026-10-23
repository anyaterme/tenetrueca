from django import forms
from django.conf import settings

from assistant.models import PreparationPhoto
from assistant.preparations import digest
from catalog.models import Category, public_category_ids
from publications.forms import HierarchicalPublicationCategoryField, MultiplePublicationPhotoField


class PreparationReviewForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    title = forms.CharField(label='Nombre del objeto', max_length=180)
    description = forms.CharField(label='Descripción', widget=forms.Textarea(attrs={'rows': 3}))
    category = HierarchicalPublicationCategoryField(
        label='Categoría y subcategoría', queryset=Category.objects.none(),
    )
    accepted_suggestions = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    discarded_suggestions = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    photos = MultiplePublicationPhotoField(required=False, label='Añadir fotografías')
    remove_photos = forms.ModelMultipleChoiceField(queryset=PreparationPhoto.objects.none(), required=False,
                                                   widget=forms.CheckboxSelectMultiple,
                                                   label='Eliminar fotografías')
    primary_photo = forms.ChoiceField(required=False, label='Fotografía principal')
    manual_fields = forms.MultipleChoiceField(required=False, choices=[(key, key) for key in
                                            ('title', 'description', 'category_id')])

    def __init__(self, *args, preparation, evaluation, action=None, **kwargs):
        self.preparation, self.evaluation, self.action = preparation, evaluation, action
        self.photo_first = preparation.working_data.get('flow') == 'photo_first'
        self.suggestions = evaluation.result.get('suggestions', []) if evaluation and evaluation.status == 'completed' else []
        if self.photo_first:
            self.suggestions = []
        initial = dict(preparation.working_data)
        initial.update({'category': initial['category_id'], 'version': digest(preparation.working_data),
                        'accepted_suggestions': preparation.selected_suggestions,
                        'discarded_suggestions': initial.get('discarded_suggestions', [])})
        for key, value in initial.get('responses', {}).items():
            initial[f'suggestion_{key}'] = value
        kwargs.setdefault('initial', initial)
        if args and args[0] is not None and action in ('confirm_all', 'confirm_none', 'accept_applicable'):
            bound = args[0].copy()
            selected = [item['id'] for item in self.suggestions] if action == 'confirm_all' else []
            if action == 'accept_applicable':
                selected = list(bound.getlist('accepted_suggestions') if hasattr(bound, 'getlist')
                                else bound.get('accepted_suggestions', []))
                selected += [item['id'] for item in self.suggestions
                             if item['kind'] == 'replace' and item['field'] != 'title'
                             and item['id'] not in selected]
            if hasattr(bound, 'setlist'):
                bound.setlist('accepted_suggestions', selected)
            else:
                bound['accepted_suggestions'] = selected
            args = (bound, *args[1:])
        super().__init__(*args, **kwargs)
        if self.photo_first and action == 'analyze_photos':
            for key in ('title', 'description', 'category'):
                self.fields[key].required = False
        categories = Category.objects.filter(pk__in=public_category_ids()).select_related('parent')
        self.fields['category'].queryset = categories
        self.fields['accepted_suggestions'].choices = [(item['id'], item['reason']) for item in self.suggestions]
        self.fields['discarded_suggestions'].choices = self.fields['accepted_suggestions'].choices
        self.fields['title'].help_text = 'Indica un nombre claro y breve.'
        self.fields['description'].help_text = 'Describe el estado y las características que conoces del objeto.'
        self.fields['category'].help_text = 'Selecciona la clasificación que mejor se ajuste al objeto.'
        self.fields['remove_photos'].queryset = preparation.photos.all()
        self.fields['remove_photos'].label_from_instance = lambda photo: f'Foto {photo.sort_order + 1}'
        self.fields['primary_photo'].choices = [('', 'Conservar la principal')] + [
            (str(photo.pk), f'Foto {photo.sort_order + 1}') for photo in preparation.photos.all()
        ]
        uploads = self.files.getlist('photos') if hasattr(self.files, 'getlist') else self.files.get('photos', [])
        if uploads and not isinstance(uploads, (list, tuple)):
            uploads = [uploads]
        self.fields['primary_photo'].choices += [(f'new:{i}', f'Nueva foto {i + 1}') for i in range(len(uploads or []))]
        for item in self.suggestions:
            if item['kind'] == 'photo':
                continue
            key = f'suggestion_{item["id"]}'
            if item['field'] == 'category':
                field = HierarchicalPublicationCategoryField(
                    queryset=categories, required=False, label='Clasificación propuesta',
                    initial=item['category_id'],
                )
            else:
                field = forms.CharField(
                    required=False, label='Texto propuesto' if item['kind'] == 'replace' else 'Tu respuesta',
                    max_length=180 if item['field'] == 'title' else 4000,
                    widget=forms.TextInput() if item['field'] == 'title' else forms.Textarea(attrs={'rows': 2}),
                    initial=item.get('proposed_text') or '',
                )
            self.fields[key] = field

    def clean(self):
        data = super().clean()
        selected = data.get('accepted_suggestions', [])
        if self.action == 'confirm_all':
            selected = [item['id'] for item in self.suggestions]
        elif self.action == 'confirm_none':
            selected = []
        data['accepted_suggestions'] = selected
        data['discarded_suggestions'] = [key for key in data.get('discarded_suggestions', []) if key not in selected]
        final = {'title': data.get('title', ''), 'description': data.get('description', ''),
                 'category_id': data['category'].pk if data.get('category') else None}
        for item in sorted(self.suggestions, key=lambda item: item['kind'] == 'information'):
            if item['id'] not in selected:
                self._errors.pop(f'suggestion_{item["id"]}', None)
                continue
            if item['kind'] == 'photo':
                removed_ids = {p.pk for p in data.get('remove_photos') or []}
                remaining_added = set(self.preparation.working_data.get('added_photos', [])) - removed_ids
                if not data.get('photos') and not remaining_added:
                    self.add_error('photos', 'Añade una fotografía para aceptar esta sugerencia.')
                continue
            key = f'suggestion_{item["id"]}'
            value = data.get(key)
            if not value:
                self.add_error(key, 'Completa este campo para aceptar la sugerencia.')
            elif item['field'] == 'category':
                final['category_id'] = value.pk
            elif item['kind'] == 'information':
                final['description'] += '\n\n' + value
            else:
                final[item['field']] = value
        if final['category_id'] and not (self.photo_first and self.action == 'analyze_photos'):
            category = Category.objects.get(pk=final['category_id'])
            if category.children.filter(is_active=True).exists():
                self.add_error('category', 'Selecciona también una subcategoría.')
        removed = data.get('remove_photos')
        count = self.preparation.photos.count() - (removed.count() if removed is not None else 0) + len(data.get('photos') or [])
        if not 1 <= count <= settings.PUBLICATION_PHOTO_MAX_COUNT:
            self.add_error('photos', f'Conserva entre 1 y {settings.PUBLICATION_PHOTO_MAX_COUNT} fotografías.')
        self.final_data = final
        return data

    def working_data(self):
        data = self.cleaned_data
        removed = {photo.pk for photo in data['remove_photos']}
        added = self.preparation.working_data.get('added_photos', [])
        result = {
            'title': data['title'], 'description': data['description'],
            'category_id': data['category'].pk if data.get('category') else None,
            'responses': {item['id']: (data[f'suggestion_{item["id"]}'].pk
                                     if item['field'] == 'category' and data.get(f'suggestion_{item["id"]}')
                                     else data.get(f'suggestion_{item["id"]}', ''))
                          for item in self.suggestions if item['kind'] != 'photo'},
            'added_photos': [pk for pk in added if pk not in removed],
            'discarded_suggestions': data.get('discarded_suggestions', []),
        }
        if self.photo_first:
            previous = self.preparation.working_data
            manual = set(previous.get('manual_fields', [])) | set(data.get('manual_fields', []))
            manual.update(key for key in ('title', 'description', 'category_id') if result[key] != previous.get(key))
            result.update(flow='photo_first', ai_prefill=previous.get('ai_prefill'), manual_fields=sorted(manual))
        return result

    def suggestion_rows(self):
        selected = set(self['accepted_suggestions'].value() or [])
        discarded = set(self['discarded_suggestions'].value() or []) - selected
        def category_label(value):
            try:
                category = Category.objects.filter(pk=value).first()
            except (ValueError, TypeError):
                category = None
            return self.fields['category'].label_from_instance(category) if category else ''
        labels = {'title': 'Nombre del objeto', 'description': 'Descripción',
                  'category': 'Categoría y subcategoría', 'photos': 'Fotografías'}
        icons = {'title': 'edit', 'description': 'file-check', 'category': 'tag', 'photos': 'image'}
        rows = []
        for item in self.suggestions:
            field = self[f'suggestion_{item["id"]}'] if item['kind'] != 'photo' else None
            proposed = field.value() if field else ''
            expected = item.get('category_id') if item['field'] == 'category' else item.get('proposed_text') or ''
            modified = bool(field and str(proposed or '') != str(expected or ''))
            rows.append({**item, 'accepted': item['id'] in selected, 'discarded': item['id'] in discarded,
                         'modified': modified, 'input': field, 'label': labels[item['field']],
                         'icon': icons[item['field']], 'requires_confirmation': item['field'] == 'title',
                         'applicable': item['kind'] == 'replace' and item['field'] != 'title',
                         'proposal': category_label(proposed) if item['field'] == 'category' else proposed or '',
                         'original': category_label(self['category'].value()) if item['field'] == 'category'
                         else self[item['field']].value() if item['field'] != 'photos' else '',
                         })
        return rows

    def photo_rows(self):
        removed = {str(pk) for pk in self['remove_photos'].value() or []}
        primary = self['primary_photo'].value()
        added = set(self.preparation.working_data.get('added_photos', []))
        return [{'photo': photo, 'removed': str(photo.pk) in removed,
                 'added': photo.pk in added,
                 'primary': str(photo.pk) == primary if primary else photo.is_primary}
                for photo in self.preparation.photos.all()]
