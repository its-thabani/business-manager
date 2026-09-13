from django.contrib import admin

from apps.sales.models import Customer, Order, OrderLine, Refund, RefundLine


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    fields = ("title", "variant_title", "sku", "quantity", "unit_price", "discount_amount")
    readonly_fields = fields


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    fields = ("refunded_on", "amount", "note")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "placed_on",
        "financial_status",
        "fulfilment_status",
        "total_price",
        "customer",
        "is_test",
    )
    list_filter = ("financial_status", "fulfilment_status", "is_test", "shipping_country")
    search_fields = ("name", "email", "shopify_id")
    date_hierarchy = "placed_on"
    inlines = (OrderLineInline, RefundInline)
    readonly_fields = (
        "shopify_id",
        "placed_at",
        "placed_on",
        "raw",
        "last_synced_at",
    )
    list_select_related = ("customer",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("display_name", "email", "country", "shopify_orders_count")
    search_fields = ("email", "first_name", "last_name", "shopify_id")


class RefundLineInline(admin.TabularInline):
    model = RefundLine
    extra = 0


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("order", "refunded_on", "amount", "restocked")
    date_hierarchy = "refunded_on"
    inlines = (RefundLineInline,)
    list_select_related = ("order",)
