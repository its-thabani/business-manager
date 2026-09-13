"""Postage charged versus supplier cost. Missing cost is never treated as £0."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.shipping import (
    compute_shipping,
    rank_countries,
    rank_shipping_orders,
)
from apps.core.periods import DateRange
from apps.supplier.models import SupplierOrder

pytestmark = pytest.mark.django_db

JUNE = DateRange(date(2026, 6, 1), date(2026, 6, 30), "June")


def _link_postage(order, cost: str, *, ref: str = "IT-1"):
    return SupplierOrder.objects.create(
        supplier_reference=ref,
        order=order,
        product_cost=Decimal("8.00"),
        shipping_cost=Decimal(cost),
    )


def test_charged_minus_known_cost_is_the_postage_result(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    order = make_order(lines=[(product.variants.get(), 1, "34.99")], shipping_charged="3.95")
    _link_postage(order, "2.95")

    metrics = compute_shipping(JUNE)

    assert metrics.shippable == 1
    assert metrics.charged == Decimal("3.95")
    assert metrics.cost == Decimal("2.95")
    assert metrics.net == Decimal("1.00")
    assert metrics.rows[0].net == Decimal("1.00")


def test_missing_supplier_postage_is_unknown_not_zero(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    make_order(lines=[(product.variants.get(), 1, "34.99")], shipping_charged="3.95")

    metrics = compute_shipping(JUNE)

    assert metrics.cost is None
    assert metrics.net is None
    assert metrics.cost_missing == 1
    assert metrics.rows[0].cost is None


def test_digital_downloads_are_not_free_shipping(make_product, make_order):
    product = make_product(variants=[("Digital", "", "0.00")])
    free = make_order(
        lines=[(product.variants.get(), 1, "0.00")],
        shipping_charged="0.00",
    )
    free.lines.update(requires_shipping=False)

    metrics = compute_shipping(JUNE)

    assert metrics.digital == 1
    assert metrics.shippable == 0
    assert metrics.free_postage == 0
    assert metrics.charged == Decimal("0.00")


def test_free_postage_absorbs_known_supplier_cost(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    order = make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        shipping_charged="0.00",
        shipping_country="GB",
    )
    _link_postage(order, "2.95")

    metrics = compute_shipping(JUNE)

    assert metrics.free_postage == 1
    assert metrics.free_rate_pct == Decimal("100.00")
    assert metrics.free_cost == Decimal("2.95")
    assert metrics.net == Decimal("-2.95")


def test_free_postage_without_a_cost_does_not_invent_absorbed(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    make_order(lines=[(product.variants.get(), 1, "34.99")], shipping_charged="0.00")

    metrics = compute_shipping(JUNE)

    assert metrics.free_postage == 1
    assert metrics.free_cost is None
    assert metrics.free_cost_missing == 1


def test_partial_supplier_postage_stays_unknown(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    order = make_order(lines=[(product.variants.get(), 1, "34.99")], shipping_charged="3.95")
    SupplierOrder.objects.create(
        supplier_reference="IT-known",
        order=order,
        shipping_cost=Decimal("2.95"),
    )
    SupplierOrder.objects.create(
        supplier_reference="IT-blank",
        order=order,
        shipping_cost=None,
    )

    metrics = compute_shipping(JUNE)

    assert metrics.rows[0].cost is None
    assert metrics.cost is None


def test_countries_aggregate_charged_and_known_cost(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    gb = make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="3.95",
        shipping_country="GB",
    )
    _link_postage(gb, "2.95", ref="IT-gb")
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        shipping_country="US",
        name="#2002",
    )

    metrics = compute_shipping(JUNE)
    by_country = {row.country: row for row in metrics.countries}

    assert by_country["GB"].charged == Decimal("3.95")
    assert by_country["GB"].cost == Decimal("2.95")
    assert by_country["GB"].net == Decimal("1.00")
    assert by_country["US"].free_orders == 1
    assert by_country["US"].cost is None


def test_countries_rank_by_charged(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="1.00",
        shipping_country="FR",
    )
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="5.00",
        shipping_country="GB",
        name="#2003",
    )

    ranked = rank_countries(compute_shipping(JUNE).countries, sort="charged")

    assert [row.country for row in ranked] == ["GB", "FR"]


def test_orders_with_unknown_cost_rank_last(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    known = make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="3.95",
        name="#known",
    )
    _link_postage(known, "2.00")
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="9.00",
        name="#unknown",
    )

    ranked = rank_shipping_orders(compute_shipping(JUNE).rows, sort="cost")

    assert [row.name for row in ranked] == ["#known", "#unknown"]
