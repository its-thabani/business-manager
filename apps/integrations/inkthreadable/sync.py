"""Persist Inkthreadable orders and the products implied by their line items.

Inkthreadable has no catalogue listing endpoint. The reliable source of
supplier cost is the order itself: each item's ``price`` is what they charged
for that print, and ``summary.shippingPrice`` is postage. Products and variants
are derived from those items so they can later be mapped to Shopify variants.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.catalog.models import normalise_colour, normalise_size
from apps.core.money import ZERO
from apps.integrations.inkthreadable.client import InkthreadableClient
from apps.integrations.models import SyncResource, SyncRun, SyncService
from apps.integrations.parsing import local_date, money, parse_datetime
from apps.integrations.sync import sync_run
from apps.sales.models import Order
from apps.supplier.models import (
    SupplierOrder,
    SupplierOrderStatus,
    SupplierProduct,
    SupplierVariant,
    SupplierVariantCost,
)

logger = logging.getLogger(__name__)

# Inkthreadable's line ``title`` is often the Shopify design name
# ("Perfect Peace Graphic T-Shirt - S / White"), not the blank
# ("The AWDis 180 T-shirt"). Group catalogue rows by the product code
# in ``pn`` (AT002, JH001, …) so Mapping can actually find the garment.
KNOWN_BLANKS = {
    "AT002": ("The AWDis 180 T-shirt", "AWDis"),
    "AT001": ("AWDis T-shirt", "AWDis"),
    "JH001": ("AWDis College Hoodie", "AWDis"),
    "JH030": ("AWDis Sweatshirt", "AWDis"),
}

_STATUS = {
    "received": SupplierOrderStatus.PENDING,
    "in progress": SupplierOrderStatus.IN_PRODUCTION,
    "in_production": SupplierOrderStatus.IN_PRODUCTION,
    "paid": SupplierOrderStatus.IN_PRODUCTION,
    "shipped": SupplierOrderStatus.SHIPPED,
    "delivered": SupplierOrderStatus.DELIVERED,
    "refunded": SupplierOrderStatus.CANCELLED,
    "cancelled": SupplierOrderStatus.CANCELLED,
}


def _country_code(payload: dict) -> str:
    country = ((payload.get("shipping_address") or {}).get("country") or "").strip()
    return country.upper() if len(country) == 2 else ""


def _status(raw: object) -> str:
    return _STATUS.get(str(raw or "").strip().lower(), SupplierOrderStatus.UNKNOWN)


def _option(item: dict, *names: str) -> str:
    for option in item.get("options") or []:
        kind = str(option.get("type") or option.get("name") or "").strip().casefold()
        if kind in {n.casefold() for n in names}:
            return str(option.get("value") or "").strip()
    return ""


def garment_code(sku: str) -> str:
    """Inkthreadable product code from a variant SKU.

    ``AT002-DBL-L`` → ``AT002``. ``OLD-JH001-JBK-M`` → ``JH001``.
    """
    parts = [p for p in (sku or "").upper().replace("_", "-").split("-") if p]
    if parts and parts[0] == "OLD":
        parts = parts[1:]
    if not parts:
        return ""
    if parts[0] == "AS" and len(parts) > 1:
        return f"{parts[0]}-{parts[1]}"
    return parts[0]


def _looks_like_shopify_line_title(title: str) -> bool:
    """True when Inkthreadable stored the shop design, not the blank name."""
    return " / " in title and " - " in title


def _supplier_product_for_item(*, sku: str, title: str) -> SupplierProduct:
    code = garment_code(sku)
    known_name, known_brand = KNOWN_BLANKS.get(code, ("", ""))
    if code:
        found = SupplierProduct.objects.filter(supplier_id=code).first()
        if found:
            return found
        if known_name:
            found = SupplierProduct.objects.filter(name=known_name).first()
            if found:
                _stamp_blank_identity(found, code=code, brand=known_brand)
                return found

    if known_name:
        name = known_name
    elif title and not _looks_like_shopify_line_title(title):
        name = title.strip()
    else:
        name = code or title.strip() or "Unknown garment"

    product = SupplierProduct.objects.filter(name=name).first()
    if product is None:
        return SupplierProduct.objects.create(
            name=name[:300],
            supplier_id=code,
            brand=known_brand,
        )
    _stamp_blank_identity(product, code=code, brand=known_brand)
    return product


def _stamp_blank_identity(product: SupplierProduct, *, code: str, brand: str) -> None:
    fields = []
    if code and not product.supplier_id:
        product.supplier_id = code
        fields.append("supplier_id")
    if brand and not product.brand:
        product.brand = brand
        fields.append("brand")
    if fields:
        fields.append("updated_at")
        product.save(update_fields=fields)


def upsert_supplier_variant(item: dict, *, when) -> SupplierVariant | None:
    """Create or update the catalogue row implied by one order line."""
    sku = (item.get("pn") or item.get("sku") or "").strip()
    title = (item.get("title") or item.get("description") or sku or "Unknown garment").strip()
    size = normalise_size(_option(item, "Size", "Sizes", "Garment Size") or "")
    colour = normalise_colour(_option(item, "Color", "Colour", "Colors", "Colours") or "")
    unit_cost = money(item.get("price"))

    if not sku and not title:
        return None

    product = _supplier_product_for_item(sku=sku, title=title)
    # Seeing it on a new order means it is still in use. Never delete an old
    # blank — Inkthreadable phases garments out, and historical costs must stay.
    product.last_synced_at = timezone.now()
    if product.is_discontinued:
        product.is_discontinued = False
        product.save(update_fields=["last_synced_at", "is_discontinued", "updated_at"])
    else:
        product.save(update_fields=["last_synced_at", "updated_at"])

    lookup = {"product": product}
    if sku:
        variant, _ = SupplierVariant.objects.update_or_create(
            sku=sku,
            defaults={**lookup, "supplier_id": sku, "size": size, "colour": colour, "last_synced_at": timezone.now()},
        )
    else:
        variant, _ = SupplierVariant.objects.get_or_create(
            product=product,
            size=size,
            colour=colour,
            defaults={"last_synced_at": timezone.now()},
        )

    if unit_cost is not None and when is not None:
        SupplierVariantCost.objects.get_or_create(
            supplier_variant=variant,
            effective_from=when,
            defaults={"unit_cost": unit_cost, "source": "api", "note": "From Inkthreadable order line"},
        )
    return variant


def _external_id_tokens(payload: dict) -> list[str]:
    """Shopify order names Inkthreadable might have put on this fulfilment.

    Their ``external_id`` is often ``#1440/14783189090681`` — the Shopify order
    name plus a fulfilment id. We try the full string first, then the part
    before ``/``. Wix / Etsy / site refs are left as-is and simply will not
    match a Shopify order, which is correct.
    """
    external = str(payload.get("external_id") or "").strip()
    if not external:
        return []
    tokens = [external]
    if "/" in external:
        head = external.split("/", 1)[0].strip()
        if head and head not in tokens:
            tokens.append(head)
    return tokens


def match_shopify_order(payload: dict) -> Order | None:
    """Find the Shopify order this fulfilment belongs to, if we already have it.

    Returns ``None`` rather than guessing. A Wix or Etsy Inkthreadable order
    must stay unlinked.
    """
    for token in _external_id_tokens(payload):
        found = (
            Order.objects.filter(shopify_id=token).first()
            or Order.objects.filter(name=token).first()
        )
        if found is not None:
            return found
        # Only treat a number as a Shopify name when Inkthreadable already used
        # a hash (`#1440`). Bare digits are Wix / older store refs and must not
        # be forced onto `#1440`.
        if token.startswith("#"):
            found = Order.objects.filter(name=f"#{token.lstrip('#')}").first()
            if found is not None:
                return found
        if token.isdigit():
            found = Order.objects.filter(order_number=int(token)).first()
            if found is not None:
                return found
    return None


def rebuild_blanks_from_stored_orders() -> dict[str, int]:
    """Derive blanks and costs from Inkthreadable payloads already in the database.

    Does not call the API. Needed because orders can be loaded (or synced
    incrementally) without ever creating catalogue rows — which is how AT002
    tees were invoiced but missing from Mapping.
    """
    created_before = SupplierProduct.objects.count()
    variants_before = SupplierVariant.objects.count()
    items = 0
    failed = 0
    for record in SupplierOrder.objects.iterator():
        when = local_date(record.placed_at) if record.placed_at else None
        for item in (record.raw or {}).get("items") or []:
            items += 1
            try:
                upsert_supplier_variant(item, when=when)
            except Exception:
                failed += 1
                logger.exception(
                    "inkthreadable: failed to rebuild variant from order %s",
                    record.supplier_reference,
                )
    return {
        "items": items,
        "failed": failed,
        "blanks_added": SupplierProduct.objects.count() - created_before,
        "variants_added": SupplierVariant.objects.count() - variants_before,
    }


def relink_supplier_orders() -> dict[str, int]:
    """Attach stored Inkthreadable orders to Shopify using the saved payload.

    Does not call the API and does not invent matches. Existing links are left
    alone. Also rebuilds blanks from those payloads so Mapping has AT002 etc.
    Safe to re-run after a Shopify sync lands new order names.
    """
    blanks = rebuild_blanks_from_stored_orders()
    linked = 0
    already = 0
    unmatched = 0
    for record in SupplierOrder.objects.iterator():
        if record.order_id:
            already += 1
            continue
        order = match_shopify_order(record.raw or {})
        if order is None:
            unmatched += 1
            continue
        record.order = order
        record.save(update_fields=["order", "updated_at"])
        linked += 1
    return {
        "linked": linked,
        "already": already,
        "unmatched": unmatched,
        "blanks_added": blanks["blanks_added"],
        "variants_added": blanks["variants_added"],
    }


def upsert_supplier_order(payload: dict) -> tuple[SupplierOrder, bool]:
    reference = str(payload.get("id") or payload.get("external_id") or "").strip()
    if not reference:
        raise ValueError("Inkthreadable order has no id")

    summary = payload.get("summary") or {}
    shipping = payload.get("shipping") or {}
    placed_at = parse_datetime(payload.get("created_at"))
    placed_on = local_date(placed_at) if placed_at else None

    product_cost = money(summary.get("subtotalPrice"))
    if product_cost is None:
        items = payload.get("items") or []
        parts = [money(item.get("price")) for item in items]
        quantities = [item.get("quantity") or 1 for item in items]
        if any(part is not None for part in parts):
            product_cost = sum(
                ((part or ZERO) * (qty or 1) for part, qty in zip(parts, quantities)),
                ZERO,
            )

    shipping_cost = money(summary.get("shippingPrice"))
    tax = money(summary.get("totalTax"))
    total = money(summary.get("total"))

    defaults = {
        "order": match_shopify_order(payload),
        "status": _status(payload.get("status")),
        "placed_at": placed_at,
        "shipped_at": parse_datetime(shipping.get("shipped_at") or payload.get("shipped_at")),
        "product_cost": product_cost,
        "shipping_cost": shipping_cost,
        "tax": tax,
        "total_cost": total,
        "tracking_number": shipping.get("trackingNumber") or payload.get("trackingNumber") or "",
        "tracking_url": shipping.get("shiplabel_url") or "",
        "shipping_country": _country_code(payload),
        "raw": payload,
        "last_synced_at": timezone.now(),
    }
    supplier_order, created = SupplierOrder.objects.update_or_create(
        supplier_reference=reference,
        defaults=defaults,
    )

    for item in payload.get("items") or []:
        try:
            upsert_supplier_variant(item, when=placed_on)
        except Exception:
            logger.exception("inkthreadable: failed to persist variant from order %s", reference)

    return supplier_order, created


def sync_orders(client: InkthreadableClient | None = None, *, full: bool = False) -> SyncRun:
    # Inkthreadable's listing endpoint is slow; small pages and a long timeout
    # are more reliable than asking for 100 rows at once.
    client = client or InkthreadableClient(timeout=90)
    previous = None if full else SyncRun.latest_success(SyncService.INKTHREADABLE, SyncResource.ORDERS)
    since_id = int(previous.cursor) if previous and previous.cursor.isdigit() else None

    with sync_run(SyncService.INKTHREADABLE, SyncResource.ORDERS) as (run, stats):
        latest_id = since_id or 0
        for payload in client.iter_orders(since_id=since_id, page_size=10):
            stats.seen += 1
            try:
                record, created = upsert_supplier_order(payload)
                stats.mark(created)
            except Exception:
                stats.failed += 1
                logger.exception("inkthreadable: failed to persist order %s", payload.get("id"))
                continue
            try:
                latest_id = max(latest_id, int(record.supplier_reference))
            except (TypeError, ValueError):
                pass
        stats.cursor = str(latest_id) if latest_id else ""
        stats.extra["since_id"] = since_id
        try:
            stats.extra["store_count"] = client.count_orders()
        except Exception as exc:  # noqa: BLE001
            stats.extra["store_count_error"] = str(exc)
    return run
