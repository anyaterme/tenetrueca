from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Prefetch
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from core.models import ActiveModel, TimeStampedModel


class Category(ActiveModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='children',
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        indexes = [
            models.Index(fields=['parent', 'is_active', 'sort_order']),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower('slug'),
                name='catalog_category_slug_ci_unique',
            ),
            models.CheckConstraint(
                condition=~models.Q(id=models.F('parent_id')),
                name='catalog_category_not_own_parent',
            ),
        ]
        verbose_name_plural = 'categories'

    def is_available_for_publication(self):
        category = self
        visited = set()
        while category is not None:
            if not category.is_active or category.pk in visited:
                return False
            visited.add(category.pk)
            category = category.parent
        return True

    def clean(self):
        super().clean()
        ancestor = self.parent
        visited = {self.pk} if self.pk else set()
        while ancestor is not None:
            if ancestor.pk in visited:
                raise ValidationError({'parent': 'Una categoría no puede ser antecesora de sí misma.'})
            visited.add(ancestor.pk)
            ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


def public_category_ids():
    categories = {
        category_id: {'parent_id': parent_id, 'is_active': is_active}
        for category_id, parent_id, is_active in Category.objects.values_list(
            'id', 'parent_id', 'is_active'
        )
    }
    public_ids = []
    for category_id in categories:
        current_id = category_id
        visited = set()
        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            category = categories.get(current_id)
            if category is None or not category['is_active']:
                break
            current_id = category['parent_id']
        else:
            if current_id is None:
                public_ids.append(category_id)
    return public_ids


def catalog_readiness_errors(publication, center):
    errors = []
    if publication.status != publication.Status.APPROVED or publication.approved_at is None:
        errors.append('La publicación debe estar aprobada por moderación.')
    if not publication.title.strip():
        errors.append('La publicación necesita un nombre público.')
    if not publication.description.strip():
        errors.append('La publicación necesita una descripción pública.')
    if not publication.category.is_available_for_publication():
        errors.append('La categoría debe estar activa, incluidos sus niveles superiores.')
    if not center.is_active:
        errors.append('El Punto Limpio receptor debe estar activo.')
    if center.operational_status != center.OperationalStatus.OPEN:
        errors.append('El Punto Limpio receptor debe estar operativo.')
    return errors


class Tag(ActiveModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(Lower('name'), name='catalog_tag_name_ci_unique'),
        ]

    def __str__(self):
        return self.name


class ReusableObjectQuerySet(models.QuerySet):
    def available(self):
        return self.filter(status='available').exclude(reservations__status='active')

    def public_catalog(self):
        return (
            self.available()
            .annotate(
                public_title=Trim('title'),
                public_description=Trim('description'),
            )
            .filter(
                publication__status='approved',
                publication__approved_at__isnull=False,
                received_at__isnull=False,
                validated_at__isnull=False,
                validated_by__isnull=False,
                category_node_id__in=public_category_ids(),
                category_node_id=F('publication__category_id'),
                center__is_active=True,
                center__operational_status='open',
            )
            .exclude(public_title='')
            .exclude(public_description='')
        )

    def with_public_photos(self):
        from publications.models import PublicationPhoto

        return self.prefetch_related(
            Prefetch(
                'publication__photos',
                queryset=PublicationPhoto.objects.order_by(
                    '-is_primary',
                    'sort_order',
                    'created_at',
                ),
                to_attr='public_photos',
            )
        )


class ReusableObject(TimeStampedModel):
    class Condition(models.TextChoices):
        LIKE_NEW = 'like_new', 'Como nuevo'
        GOOD = 'good', 'Buen estado'
        FAIR = 'fair', 'Estado aceptable'
        NEEDS_REPAIR = 'needs_repair', 'Necesita reparación'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente de revisión'
        PENDING_VALIDATION = 'pending_validation', 'Pendiente de validación física'
        AVAILABLE = 'available', 'Disponible'
        RESERVED = 'reserved', 'Reservado'
        DELIVERED = 'delivered', 'Entregado'
        WITHDRAWN = 'withdrawn', 'Retirado'
        REJECTED = 'rejected', 'Rechazado'

    reference = models.CharField(max_length=32, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reusable_objects',
    )
    center = models.ForeignKey(
        'locations.RecyclingCenter',
        on_delete=models.PROTECT,
        related_name='reusable_objects',
    )
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=80, db_index=True)
    category_node = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='legacy_objects',
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='legacy_objects')
    publication = models.OneToOneField(
        'publications.Publication',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='inventory_object',
    )
    condition = models.CharField(max_length=24, choices=Condition.choices)
    status = models.CharField(max_length=24, choices=Status.choices, db_index=True)
    points_cost = models.PositiveIntegerField(default=0)
    is_pack = models.BooleanField(default=False)
    weight_kg = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    dimensions = models.JSONField(default=dict, blank=True)
    internal_location = models.CharField(max_length=120, blank=True)
    received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='validated_reusable_objects',
    )
    validation_notes = models.TextField(blank=True)

    objects = ReusableObjectQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'category']),
            models.Index(fields=['center', 'status']),
            models.Index(fields=['center', 'internal_location']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(validated_at__isnull=True, validated_by__isnull=True)
                    | models.Q(validated_at__isnull=False, validated_by__isnull=False)
                ),
                name='catalog_validation_fields_together',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(publication__isnull=True)
                    | models.Q(status='pending_validation')
                    | models.Q(validated_at__isnull=False, validated_by__isnull=False)
                ),
                name='catalog_linked_object_requires_validation',
            ),
        ]

    def clean(self):
        super().clean()
        if not self.publication_id:
            return

        publication_status = (
            self._meta.get_field('publication')
            .remote_field.model.objects.filter(pk=self.publication_id)
            .values_list('status', flat=True)
            .first()
        )
        if publication_status != 'approved':
            raise ValidationError(
                {'publication': 'Solo una publicación aprobada puede incorporarse al inventario.'}
            )

    def save(self, *args, **kwargs):
        if self.publication_id and self.received_at is None:
            self.received_at = timezone.now()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.reference} · {self.title}'


class Favorite(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='favorites',
    )
    reusable_object = models.ForeignKey(
        ReusableObject,
        on_delete=models.CASCADE,
        related_name='favorited_by',
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'reusable_object'],
                name='catalog_unique_user_favorite',
            ),
        ]
        indexes = [models.Index(fields=['user', 'created_at'])]

    def __str__(self):
        return f'{self.user} · {self.reusable_object}'
