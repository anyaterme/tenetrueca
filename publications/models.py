from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.utils import timezone

from core.models import TimeStampedModel
from publications.images import normalize_publication_photo
from publications.storage import private_publication_photo_storage


def publication_photo_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    random_name = uuid4().hex
    return f'publications/{random_name[:2]}/{random_name}{extension}'


class Publication(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        PENDING_REVIEW = 'pending_review', 'Pendiente de revisión'
        CHANGES_REQUESTED = 'changes_requested', 'Cambios solicitados'
        APPROVED = 'approved', 'Aprobada'
        REJECTED = 'rejected', 'Rechazada'
        WITHDRAWN = 'withdrawn', 'Retirada'

    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='object_publications',
    )
    title = models.CharField(max_length=180)
    description = models.TextField()
    category = models.ForeignKey(
        'catalog.Category',
        on_delete=models.PROTECT,
        related_name='publications',
    )
    tags = models.ManyToManyField('catalog.Tag', blank=True, related_name='publications')
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'category']),
            models.Index(fields=['submitter', 'status']),
        ]

    def clean(self):
        super().clean()
        if not self._state.adding or not self.category_id:
            return
        category = (
            self._meta.get_field('category')
            .remote_field.model.objects.select_related('parent')
            .filter(pk=self.category_id)
            .first()
        )
        if category is not None and not category.is_available_for_publication():
            raise ValidationError({'category': 'Selecciona una categoría activa.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class PublicationPhoto(TimeStampedModel):
    publication = models.ForeignKey(
        Publication,
        on_delete=models.CASCADE,
        related_name='photos',
    )
    image = models.ImageField(
        upload_to=publication_photo_upload_to,
        storage=private_publication_photo_storage,
    )
    alt_text = models.CharField(max_length=220, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    image_format = models.CharField(max_length=8, blank=True, default='', editable=False)
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    file_size = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['sort_order', 'created_at']
        indexes = [
            models.Index(fields=['publication', 'sort_order']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['publication'],
                condition=models.Q(is_primary=True),
                name='publications_unique_primary_photo',
            ),
        ]

    def clean(self):
        super().clean()
        if not self._state.adding or not self.publication_id:
            return
        if self.publication.photos.count() >= settings.PUBLICATION_PHOTO_MAX_COUNT:
            raise ValidationError(
                {'image': f'Cada publicación admite un máximo de {settings.PUBLICATION_PHOTO_MAX_COUNT} fotografías.'}
            )

    @property
    def is_publicly_accessible(self):
        from catalog.models import ReusableObject

        return ReusableObject.objects.public_catalog().filter(
            publication_id=self.publication_id
        ).exists()

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            normalized = getattr(self.image.file, '_normalized_publication_photo', None)
            if normalized is None:
                normalized = normalize_publication_photo(self.image.file)
            self.image = normalized.content
            self.image_format = normalized.image_format
            self.width = normalized.width
            self.height = normalized.height
            self.file_size = normalized.file_size

        with transaction.atomic():
            if self.publication_id:
                Publication.objects.select_for_update().only('pk').get(pk=self.publication_id)
            self.full_clean()
            return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.publication} · foto {self.sort_order}'
