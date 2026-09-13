"""Persist Shopify products, variants, customers, orders and refunds.

Every write is an upsert keyed on Shopify's global id, so a sync can be
re-run safely. Incremental mode uses the last successful run's ``updated_at``
cursor: anything Shopify reports as changed since then is written again.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Product, ProductStatus, ProductVariant
from apps.core.money import ZERO
from apps.integrations.models import SyncResource, SyncRun, SyncService
from apps.integrations.parsing import gid_suffix, local_date, money, parse_datetime
from apps.integrations.shopify.client import ShopifyClient
from apps.integrations.sync import sync_run
from apps.sales.models import (
    Customer,
    FinancialStatus,
    FulfilmentStatus,
    Order,
    OrderLine,
    Refund,
    RefundLine,
)

logger = logging.getLogger(__name__)

_FINANCIAL = {choice.value: choice.value for choice in FinancialStatus}
_FULFILMENT = {
    "unfulfilled": FulfilmentStatus.UNFULFILLED,
    "partial": FulfilmentStatus.PARTIAL,
    "partially_fulfilled": FulfilmentStatus.PARTIAL,
    "fulfilled": FulfilmentStatus.FULFILLED,
    "restocked": FulfilmentStatus.RESTOCKED,
}
_PRODUCT_STATUS = {
    "active": ProductStatus.ACTIVE,
    "draft": ProductStatus.DRAFT,
    "archived": ProductStatus.ARCHIVED,
}


def _edges(node: dict | None, key: str) -> list[dict]:
    connection = (node or {}).get(key) or {}
    return [edge.get("node") or {} for edge in connection.get("edges") or []]


def _enum(mapping: dict, raw: object, default: str) -> str:
    if not raw:
        return default
    return mapping.get(str(raw).strip().lower(), default)


def upsert_product(payload: dict) -> tuple[Product, bool]:
    shopify_id = payload.get("id") or ""
    defaults = {
        "title": payload.get("title") or "Untitled product",
        "handle": payload.get("handle") or "",
        "product_type": payload.get("productType") or "",
        "vendor": payload.get("vendor") or "",
        "status": _enum(_PRODUCT_STATUS, payload.get("status"), ProductStatus.ACTIVE),
        "tags": payload.get("tags") or [],
        "published_at": parse_datetime(payload.get("publishedAt")),
        "shopify_created_at": parse_datetime(payload.get("createdAt")),
        "shopify_updated_at": parse_datetime(payload.get("updatedAt")),
        "last_synced_at": timezone.now(),
    }
    product, created = Product.objects.update_or_create(shopify_id=shopify_id, defaults=defaults)

    seen_ids = []
    for position, variant_payload in enumerate(_edges(payload, "variants"), start=1):
        variant, _ = upsert_variant(product, variant_payload, position=position)
        seen_ids.append(variant.pk)
    # Variants that Shopify no longer returns are left in place: they may still
    # be referenced by historical order lines.
    return product, created


def upsert_variant(product: Product, payload: dict, *, position: int) -> tuple[ProductVariant, bool]:
    options = {
        option.get("name"): option.get("value")
        for option in payload.get("selectedOptions") or []
        if option.get("name")
    }
    variant, created = ProductVariant.objects.update_or_create(
        shopify_id=payload.get("id") or None,
        defaults={
            "product": product,
            "title": payload.get("title") or "",
            "sku": payload.get("sku") or "",
            "barcode": payload.get("barcode") or "",
            "position": position,
            "price": money(payload.get("price")) or ZERO,
            "compare_at_price": money(payload.get("compareAtPrice")),
            "options": options,
            "inventory_quantity": payload.get("inventoryQuantity"),
            "shopify_unit_cost": money((payload.get("inventoryItem") or {}).get("unitCost")),
            "last_synced_at": timezone.now(),
        },
    )
    if created or not (variant.size or variant.colour):
        variant.derive_options()
        variant.save(update_fields=["size", "colour", "updated_at"])
    return variant, created


def upsert_customer(payload: dict | None) -> Customer | None:
    if not payload or not payload.get("id"):
        return None
    display = (payload.get("displayName") or "").strip()
    first = (payload.get("firstName") or "").strip()
    last = (payload.get("lastName") or "").strip()
    if not first and not last and display:
        parts = display.split(None, 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
    customer, _ = Customer.objects.update_or_create(
        shopify_id=payload["id"],
        defaults={
            "email": payload.get("email") or "",
            "first_name": first,
            "last_name": last,
            "shopify_orders_count": payload.get("numberOfOrders"),
            "shopify_created_at": parse_datetime(payload.get("createdAt")),
            "last_synced_at": timezone.now(),
        },
    )
    return customer


def _payment_fee(payload: dict) -> Decimal | None:
    total = ZERO
    found = False
    for txn in payload.get("transactions") or []:
        if str(txn.get("status") or "").upper() != "SUCCESS":
            continue
        for fee in txn.get("fees") or []:
            amount = money(fee)
            if amount is not None:
                total += amount
                found = True
    return total if found else None


def _tax_on_lines(line_payload: dict) -> Decimal:
    total = ZERO
    for tax in line_payload.get("taxLines") or []:
        total += money(tax.get("priceSet")) or ZERO
    return total


@transaction.atomic
def upsert_order(payload: dict) -> tuple[Order, bool]:
    shopify_id = payload.get("id") or ""
    placed_at = (
        parse_datetime(payload.get("processedAt"))
        or parse_datetime(payload.get("createdAt"))
        or timezone.now()
    )
    shipping = payload.get("shippingAddress") or {}
    customer = upsert_customer(payload.get("customer"))
    order_number = None
    if payload.get("legacyResourceId"):
        try:
            order_number = int(payload["legacyResourceId"])
        except (TypeError, ValueError):
            order_number = None

    defaults = {
        "name": payload.get("name") or f"#{gid_suffix(shopify_id)}",
        "order_number": order_number,
        "customer": customer,
        "email": payload.get("email") or (customer.email if customer else ""),
        "placed_at": placed_at,
        "placed_on": local_date(placed_at),
        "cancelled_at": parse_datetime(payload.get("cancelledAt")),
        "cancel_reason": payload.get("cancelReason") or "",
        "currency": payload.get("currencyCode") or "GBP",
        "financial_status": _enum(
            _FINANCIAL, payload.get("displayFinancialStatus"), FinancialStatus.UNKNOWN
        ),
        "fulfilment_status": _enum(
            _FULFILMENT, payload.get("displayFulfillmentStatus"), FulfilmentStatus.UNKNOWN
        ),
        "total_discounts": money(payload.get("totalDiscountsSet")) or ZERO,
        "shipping_charged": money(payload.get("totalShippingPriceSet")) or ZERO,
        "total_tax": money(payload.get("totalTaxSet")) or ZERO,
        "total_price": money(payload.get("totalPriceSet")) or ZERO,
        "payment_fee": _payment_fee(payload),
        "is_test": bool(payload.get("test")),
        "source_name": payload.get("sourceName") or "",
        "shipping_country": shipping.get("countryCodeV2") or "",
        "discount_codes": payload.get("discountCodes") or [],
        "tags": payload.get("tags") or [],
        "raw": payload,
        "last_synced_at": timezone.now(),
    }
    order, created = Order.objects.update_or_create(shopify_id=shopify_id, defaults=defaults)
    _sync_lines(order, payload)
    _sync_refunds(order, payload)
    return order, created


def _sync_lines(order: Order, payload: dict) -> None:
    keep = []
    for line_payload in _edges(payload, "lineItems"):
        variant = None
        product = None
        variant_id = (line_payload.get("variant") or {}).get("id")
        product_id = (line_payload.get("product") or {}).get("id")
        if variant_id:
            variant = ProductVariant.objects.filter(shopify_id=variant_id).first()
            if variant:
                product = variant.product
        if product is None and product_id:
            product = Product.objects.filter(shopify_id=product_id).first()

        defaults = {
            "order": order,
            "variant": variant,
            "product": product,
            "title": line_payload.get("title") or line_payload.get("name") or "Line item",
            "variant_title": line_payload.get("variantTitle") or "",
            "sku": line_payload.get("sku") or "",
            "quantity": line_payload.get("quantity") or 0,
            "unit_price": money(line_payload.get("originalUnitPriceSet")) or ZERO,
            "discount_amount": money(line_payload.get("totalDiscountSet")) or ZERO,
            "tax_amount": _tax_on_lines(line_payload),
            "requires_shipping": bool(line_payload.get("requiresShipping", True)),
        }
        shopify_id = line_payload.get("id") or ""
        if shopify_id:
            line, _ = OrderLine.objects.update_or_create(shopify_id=shopify_id, defaults=defaults)
        else:
            line = OrderLine.objects.create(shopify_id="", **defaults)
        keep.append(line.pk)

    if keep:
        order.lines.exclude(pk__in=keep).delete()
    else:
        order.lines.all().delete()


def _sync_refunds(order: Order, payload: dict) -> None:
    keep = []
    for refund_payload in payload.get("refunds") or []:
        refunded_at = parse_datetime(refund_payload.get("createdAt")) or order.placed_at
        restocked = any(
            (edge.get("node") or {}).get("restocked")
            for edge in ((refund_payload.get("refundLineItems") or {}).get("edges") or [])
        )
        refund, _ = Refund.objects.update_or_create(
            shopify_id=refund_payload.get("id") or None,
            defaults={
                "order": order,
                "refunded_at": refunded_at,
                "refunded_on": local_date(refunded_at),
                "amount": money(refund_payload.get("totalRefundedSet")) or ZERO,
                "note": refund_payload.get("note") or "",
                "restocked": restocked,
                "raw": refund_payload,
            },
        )
        _sync_refund_lines(refund, refund_payload)
        keep.append(refund.pk)
    if keep:
        order.refunds.exclude(pk__in=keep).delete()
    elif payload.get("refunds") == []:
        order.refunds.all().delete()


def _sync_refund_lines(refund: Refund, payload: dict) -> None:
    refund.lines.all().delete()
    for item in _edges(payload, "refundLineItems"):
        line_id = (item.get("lineItem") or {}).get("id") or ""
        order_line = OrderLine.objects.filter(order=refund.order, shopify_id=line_id).first()
        if order_line is None:
            continue
        RefundLine.objects.create(
            refund=refund,
            order_line=order_line,
            quantity=item.get("quantity") or 0,
            amount=money(item.get("subtotalSet")) or ZERO,
        )


def sync_products(client: ShopifyClient | None = None, *, full: bool = False) -> SyncRun:
    client = client or ShopifyClient()
    previous = None if full else SyncRun.latest_success(SyncService.SHOPIFY, SyncResource.PRODUCTS)
    updated_after = previous.cursor if previous and previous.cursor else None

    with sync_run(SyncService.SHOPIFY, SyncResource.PRODUCTS) as (run, stats):
        latest = updated_after or ""
        for payload in client.iter_products(updated_after=updated_after):
            stats.seen += 1
            try:
                _, created = upsert_product(payload)
                stats.mark(created)
            except Exception:
                stats.failed += 1
                logger.exception("shopify: failed to persist product %s", payload.get("id"))
                continue
            updated = payload.get("updatedAt") or ""
            if updated > latest:
                latest = updated
        stats.cursor = latest
        stats.extra["updated_after"] = updated_after or ""
        stats.extra["store_count"] = client.count_products()
    return run


def sync_orders(client: ShopifyClient | None = None, *, full: bool = False) -> SyncRun:
    client = client or ShopifyClient()
    previous = None if full else SyncRun.latest_success(SyncService.SHOPIFY, SyncResource.ORDERS)
    updated_after = previous.cursor if previous and previous.cursor else None

    with sync_run(SyncService.SHOPIFY, SyncResource.ORDERS) as (run, stats):
        latest = updated_after or ""
        for payload in client.iter_orders(updated_after=updated_after):
            stats.seen += 1
            try:
                _, created = upsert_order(payload)
                stats.mark(created)
            except Exception:
                stats.failed += 1
                logger.exception("shopify: failed to persist order %s", payload.get("name") or payload.get("id"))
                continue
            updated = payload.get("updatedAt") or ""
            if updated > latest:
                latest = updated
        stats.cursor = latest
        stats.extra["updated_after"] = updated_after or ""
        stats.extra["store_count"] = client.count_orders()
    return run


def sync_all(*, full: bool = False) -> list[SyncRun]:
    client = ShopifyClient()
    return [sync_products(client, full=full), sync_orders(client, full=full)]
