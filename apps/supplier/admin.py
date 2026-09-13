from django.contrib import admin

from apps.supplier.models import (
    ProductMapping,
    SupplierOrder,
    SupplierProduct,
    SupplierVariant,
    SupplierVariantCost,
    VariantMapping,
)


class SupplierVariantInline(admin.TabularInline):
    model = SupplierVariant
    extra = 0
    fields = ("sku", "colour", "size")


class SupplierVariantCostInline(admin.TabularInline):
    model = SupplierVariantCost
    extra = 0


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "category", "is_discontinued", "last_synced_at")
    list_filter = ("is_discontinued",)
    search_fields = ("name", "brand", "supplier_id")
    inlines = (SupplierVariantInline,)


@admin.register(SupplierVariant)
class SupplierVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "sku", "colour", "size", "current_cost")
    search_fields = ("sku", "product__name")
    inlines = (SupplierVariantCostInline,)
    list_select_related = ("product",)


@admin.register(ProductMapping)
class ProductMappingAdmin(admin.ModelAdmin):
    list_display = ("product", "supplier_product", "no_supplier", "confidence")
    list_filter = ("confidence", "no_supplier")
    search_fields = ("product__title", "supplier_product__name")
    list_select_related = ("product", "supplier_product")


@admin.register(VariantMapping)
class VariantMappingAdmin(admin.ModelAdmin):
    list_display = ("product_variant", "supplier_variant", "confidence", "cost_override")
    list_filter = ("confidence",)
    search_fields = (
        "product_variant__product__title",
        "product_variant__sku",
        "supplier_variant__sku",
    )
    list_select_related = (
        "product_variant",
        "product_variant__product",
        "supplier_variant",
        "supplier_variant__product",
    )


@admin.register(SupplierOrder)
class SupplierOrderAdmin(admin.ModelAdmin):
    list_display = (
        "supplier_reference",
        "order",
        "status",
        "placed_at",
        "product_cost",
        "shipping_cost",
        "total_cost",
    )
    list_filter = ("status",)
    search_fields = ("supplier_reference", "tracking_number", "order__name")
    date_hierarchy = "placed_at"
    list_select_related = ("order",)
    readonly_fields = ("raw", "last_synced_at")
