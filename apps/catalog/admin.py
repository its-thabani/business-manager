from django.contrib import admin

from apps.catalog.models import Product, ProductGroup, ProductVariant


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ("title", "sku", "size", "colour", "price", "shopify_unit_cost", "inventory_quantity")
    readonly_fields = ("title", "sku", "shopify_unit_cost", "inventory_quantity")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "product_type", "status", "variant_count", "last_synced_at")
    list_filter = ("status", "product_type")
    search_fields = ("title", "handle", "shopify_id")
    inlines = (ProductVariantInline,)
    readonly_fields = ("shopify_id", "shopify_created_at", "shopify_updated_at", "last_synced_at")


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "title", "sku", "size", "colour", "price")
    list_filter = ("size", "colour")
    search_fields = ("sku", "title", "product__title", "shopify_id")
    list_select_related = ("product",)


@admin.register(ProductGroup)
class ProductGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order")
    filter_horizontal = ("products",)
