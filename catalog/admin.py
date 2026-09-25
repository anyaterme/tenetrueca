from django.contrib import admin

from catalog.models import Category, ReusableObject, Tag


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'slug', 'is_active', 'sort_order')
    list_editable = ('is_active', 'sort_order')
    list_filter = ('is_active', 'parent')
    search_fields = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ('parent',)
    ordering = ('sort_order', 'name')


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active')
    list_editable = ('is_active',)
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(ReusableObject)
class ReusableObjectAdmin(admin.ModelAdmin):
    list_display = ('reference', 'title', 'category', 'condition', 'status', 'center', 'points_cost')
    list_filter = ('status', 'condition', 'category', 'center')
    search_fields = ('reference', 'title', 'description', 'owner__email')
    autocomplete_fields = ('owner', 'center', 'category_node', 'publication', 'validated_by')
    filter_horizontal = ('tags',)
