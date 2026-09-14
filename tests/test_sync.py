"""Shopify and Inkthreadable records upsert without duplicating."""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

import pytest

from apps.catalog.models import Product, ProductVariant
from apps.integrations.inkthreadable.sync import (
    rebuild_blanks_from_stored_orders,
    relink_supplier_orders,
    upsert_supplier_order,
)
from apps.integrations.shopify.sync import upsert_order, upsert_product
from apps.sales.models import Order
from apps.supplier.models import SupplierOrder, SupplierProduct, SupplierVariant


def product_payload(**overrides) -> dict:
    payload = {
        "id": "gid://shopify/Product/1",
        "title": "StayLit Classic Tee",
        "handle": "classic-tee",
        "status": "ACTIVE",
        "productType": "T-Shirt",
        "vendor": "StayLit",
        "tags": ["core"],
        "createdAt": "2026-01-10T10:00:00Z",
        "updatedAt": "2026-06-01T10:00:00Z",
        "publishedAt": "2026-01-10T10:00:00Z",
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/11",
                        "title": "Black / L",
                        "sku": "TEE-BLK-L",
                        "price": "24.99",
                        "inventoryQuantity": 0,
                        "inventoryItem": {"unitCost": {"amount": "8.10", "currencyCode": "GBP"}},
                        "selectedOptions": [
                            {"name": "Colour", "value": "Black"},
                            {"name": "Size", "value": "L"},
                        ],
                    }
                }
            ]
        },
    }
    payload.update(overrides)
    return payload


def order_payload(**overrides) -> dict:
    payload = {
        "id": "gid://shopify/Order/99",
        "legacyResourceId": "1099",
        "name": "#1099",
        "email": "a@example.com",
        "createdAt": "2026-06-15T12:00:00Z",
        "updatedAt": "2026-06-15T12:05:00Z",
        "processedAt": "2026-06-15T12:00:00Z",
        "cancelledAt": None,
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "FULFILLED",
        "currencyCode": "GBP",
        "test": False,
        "sourceName": "web",
        "tags": [],
        "totalPriceSet": {"shopMoney": {"amount": "28.94"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "3.95"}},
        "totalTaxSet": {"shopMoney": {"amount": "0.00"}},
        "discountCodes": [],
        "customer": {
            "id": "gid://shopify/Customer/5",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "email": "a@example.com",
        },
        "shippingAddress": {"countryCodeV2": "GB"},
        "lineItems": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/LineItem/7",
                        "title": "StayLit Classic Tee",
                        "variantTitle": "Black / L",
                        "sku": "TEE-BLK-L",
                        "quantity": 1,
                        "originalUnitPriceSet": {"shopMoney": {"amount": "24.99"}},
                        "totalDiscountSet": {"shopMoney": {"amount": "0.00"}},
                        "requiresShipping": True,
                        "product": {"id": "gid://shopify/Product/1", "title": "StayLit Classic Tee"},
                        "variant": {"id": "gid://shopify/ProductVariant/11"},
                        "taxLines": [],
                    }
                }
            ]
        },
        "transactions": [
            {
                "kind": "SALE",
                "status": "SUCCESS",
                "fees": [{"amount": {"amount": "0.57"}, "type": "transaction_fee"}],
            }
        ],
        "refunds": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestShopifyUpsert:
    def test_a_product_and_its_variants_are_stored_once(self):
        first, created = upsert_product(product_payload())
        second, created_again = upsert_product(product_payload(title="StayLit Classic Tee (updated)"))
        assert created and not created_again
        assert first.pk == second.pk
        assert Product.objects.count() == 1
        assert ProductVariant.objects.count() == 1
        assert second.title.endswith("(updated)")
        variant = second.variants.get()
        assert variant.size == "L"
        assert variant.colour == "Black"
        assert variant.shopify_unit_cost == Decimal("8.10")

    def test_an_order_is_stored_with_lines_and_the_real_payment_fee(self):
        upsert_product(product_payload())
        order, created = upsert_order(order_payload())
        assert created
        assert order.name == "#1099"
        assert order.placed_on == date(2026, 6, 15)
        assert order.shipping_charged == Decimal("3.95")
        assert order.payment_fee == Decimal("0.57")
        assert order.lines.count() == 1
        line = order.lines.get()
        assert line.variant is not None
        assert line.unit_price == Decimal("24.99")

        upsert_order(order_payload())
        assert Order.objects.count() == 1
        assert order.lines.count() == 1

    def test_a_refund_attaches_to_the_original_line(self):
        upsert_product(product_payload())
        payload = order_payload(
            refunds=[
                {
                    "id": "gid://shopify/Refund/1",
                    "createdAt": "2026-06-20T09:00:00Z",
                    "totalRefundedSet": {"shopMoney": {"amount": "24.99"}},
                    "note": "did not fit",
                    "refundLineItems": {
                        "edges": [
                            {
                                "node": {
                                    "quantity": 1,
                                    "restocked": False,
                                    "subtotalSet": {"shopMoney": {"amount": "24.99"}},
                                    "lineItem": {"id": "gid://shopify/LineItem/7"},
                                }
                            }
                        ]
                    },
                }
            ]
        )
        order, _ = upsert_order(payload)
        assert order.refunds.count() == 1
        refund = order.refunds.get()
        assert refund.amount == Decimal("24.99")
        assert refund.lines.count() == 1
        assert refund.lines.get().order_line.shopify_id == "gid://shopify/LineItem/7"


@pytest.mark.django_db
class TestInkthreadableUpsert:
    def test_an_order_stores_actual_costs_and_catalogue_rows(self):
        payload = {
            "id": 8801,
            "external_id": "#1099",
            "created_at": "2026-06-16T08:00:00Z",
            "status": "shipped",
            "channel": "Shopify",
            "summary": {
                "currency": "GBP",
                "subtotalPrice": 9.10,
                "shippingPrice": 3.20,
                "totalTax": 0,
                "total": 12.30,
            },
            "shipping": {"trackingNumber": "AB123", "shipped_at": "2026-06-17T10:00:00Z"},
            "shipping_address": {"country": "GB"},
            "items": [
                {
                    "pn": "STTU169-BLK-L",
                    "title": "Creator 2.0",
                    "price": 9.10,
                    "quantity": 1,
                    "options": [{"type": "Color", "value": "Black"}, {"type": "Size", "value": "L"}],
                }
            ],
        }
        record, created = upsert_supplier_order(payload)
        assert created
        assert record.product_cost == Decimal("9.10")
        assert record.shipping_cost == Decimal("3.20")
        assert record.tracking_number == "AB123"
        variant = SupplierVariant.objects.get(sku="STTU169-BLK-L")
        assert variant.size == "L"
        assert variant.colour == "Black"
        assert variant.current_cost == Decimal("9.10")
        assert variant.product.supplier_id == "STTU169"
        assert variant.product.name == "Creator 2.0"

        upsert_supplier_order(payload)
        assert SupplierOrder.objects.count() == 1

    def test_awdis_180_lines_group_onto_at002_not_the_shopify_title(self):
        """Inkthreadable stores the design name; the blank is the SKU prefix."""
        for i, (title, pn, price) in enumerate(
            (
                ("Perfect Peace Graphic T-Shirt - S / White / Marbled Earth", "AT002-ACW-S", 13.44),
                ("Isaiah 9:6 Messiah T-Shirt - M / Black", "AT002-DBL-M", 13.44),
            ),
            start=1,
        ):
            upsert_supplier_order(
                {
                    "id": 9100 + i,
                    "external_id": f"#{pn}",
                    "created_at": "2026-09-01T12:00:00Z",
                    "status": "shipped",
                    "summary": {"subtotalPrice": price, "shippingPrice": 3.20, "total": price + 3.20},
                    "items": [
                        {
                            "pn": pn,
                            "title": title,
                            "price": price,
                            "quantity": 1,
                            "options": [
                                {"type": "Colour", "value": "Arctic White" if "ACW" in pn else "Black"},
                                {"type": "Size", "value": "Small" if pn.endswith("S") else "Medium"},
                            ],
                        }
                    ],
                }
            )

        blank = SupplierProduct.objects.get(supplier_id="AT002")
        assert blank.name == "The AWDis 180 T-shirt"
        assert blank.brand == "AWDis"
        assert blank.variants.count() == 2
        assert not SupplierProduct.objects.filter(name__icontains="Perfect Peace").exists()
        assert SupplierVariant.objects.get(sku="AT002-ACW-S").current_cost == Decimal("13.44")

    def test_rebuild_blanks_from_stored_orders_creates_at002(self):
        SupplierOrder.objects.create(
            supplier_reference="at002-rebuild",
            placed_at=datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc),
            raw={
                "items": [
                    {
                        "pn": "AT002-DBL-L",
                        "title": "He Loved Me First Christian Graphic T-Shirt - L / Black",
                        "price": "13.44",
                        "options": [
                            {"type": "Colour", "value": "Black"},
                            {"type": "Size", "value": "Large"},
                        ],
                    }
                ]
            },
        )
        result = rebuild_blanks_from_stored_orders()
        assert result["failed"] == 0
        blank = SupplierProduct.objects.get(supplier_id="AT002")
        assert blank.name == "The AWDis 180 T-shirt"
        assert SupplierVariant.objects.get(sku="AT002-DBL-L").current_cost == Decimal("13.44")

    def test_a_shopify_order_is_linked_by_external_id(self, make_product, make_order):
        product = make_product()
        shopify_order = make_order(lines=[(product.variants.get(), 1, "24.99")], name="#1099")
        record, _ = upsert_supplier_order(
            {
                "id": 8802,
                "external_id": "#1099",
                "created_at": "2026-06-16T08:00:00Z",
                "status": "paid",
                "summary": {"subtotalPrice": 8.10, "shippingPrice": 3.20, "total": 11.30},
                "items": [],
            }
        )
        assert record.order_id == shopify_order.pk

    def test_a_shopify_fulfilment_id_is_stripped_before_matching(self, make_product, make_order):
        product = make_product()
        shopify_order = make_order(lines=[(product.variants.get(), 1, "24.99")], name="#1440")
        record, _ = upsert_supplier_order(
            {
                "id": 2136792,
                "external_id": "#1440/14783189090681",
                "channel": "Shopify",
                "created_at": "2026-09-12T11:12:12Z",
                "status": "shipped",
                "summary": {"subtotalPrice": 9.10, "shippingPrice": 3.20, "total": 12.30},
                "items": [],
            }
        )
        assert record.order_id == shopify_order.pk

    def test_a_wix_order_is_not_forced_onto_a_shopify_order(self, make_product, make_order):
        product = make_product()
        make_order(lines=[(product.variants.get(), 1, "24.99")], name="#11613")
        record, _ = upsert_supplier_order(
            {
                "id": 9901,
                "external_id": "11613",
                "channel": "wix",
                "created_at": "2025-08-13T05:12:14Z",
                "status": "shipped",
                "summary": {"subtotalPrice": 8.00, "shippingPrice": 2.95, "total": 10.95},
                "items": [],
            }
        )
        assert record.order_id is None

    def test_relink_attaches_stored_payloads_without_overwriting(self, make_product, make_order):
        product = make_product()
        shopify_order = make_order(lines=[(product.variants.get(), 1, "24.99")], name="#1440")
        record, _ = upsert_supplier_order(
            {
                "id": 2136792,
                "external_id": "#1440/14783189090681",
                "created_at": "2026-09-12T11:12:12Z",
                "status": "shipped",
                "summary": {"subtotalPrice": 9.10, "shippingPrice": 3.20, "total": 12.30},
                "items": [],
            }
        )
        record.order = None
        record.save(update_fields=["order"])

        result = relink_supplier_orders()

        record.refresh_from_db()
        assert result["linked"] == 1
        assert record.order_id == shopify_order.pk
        assert relink_supplier_orders()["already"] == 1
