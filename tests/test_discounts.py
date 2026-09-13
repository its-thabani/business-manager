"""Discount codes give away revenue once. Missing profit stays —."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.discounts import (
    NO_CODE,
    compute_discounts,
    normalize_codes,
    rank_codes,
)
from apps.core.periods import DateRange
from apps.supplier.models import SupplierOrder

pytestmark = pytest.mark.django_db

JUNE = DateRange(date(2026, 6, 1), date(2026, 6, 30), "June")


def test_normalize_codes_strips_and_reads_objects():
    assert normalize_codes(["Collaboration ", {"code": "WELCOME10"}]) == [
        "Collaboration",
        "WELCOME10",
    ]


def test_a_code_keeps_the_whole_order_discount(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        discounts="3.50",
        discount_codes=["WELCOME10"],
    )

    metrics = compute_discounts(JUNE)

    assert metrics.discounted == 1
    assert metrics.amount == Decimal("3.50")
    assert metrics.codes[0].code == "WELCOME10"
    assert metrics.codes[0].amount == Decimal("3.50")
    assert metrics.average == Decimal("3.50")


def test_trailing_space_codes_group_together(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        discounts="20.00",
        discount_codes=["Collaboration "],
        shipping_charged="0.00",
        name="#a",
    )
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        discounts="20.00",
        discount_codes=["Collaboration"],
        shipping_charged="0.00",
        name="#b",
    )

    metrics = compute_discounts(JUNE)

    assert len(metrics.codes) == 1
    assert metrics.codes[0].code == "Collaboration"
    assert metrics.codes[0].orders == 2
    assert metrics.codes[0].amount == Decimal("40.00")


def test_automatic_markdowns_are_labelled_not_dropped(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        discounts="5.00",
        discount_codes=[],
    )

    metrics = compute_discounts(JUNE)

    assert metrics.codes[0].code == NO_CODE
    assert metrics.codes[0].amount == Decimal("5.00")


def test_two_codes_on_one_order_are_not_split(make_product, make_order):
    product = make_product(variants=[("Black", "L", "40.00")])
    make_order(
        lines=[(product.variants.get(), 1, "40.00")],
        discounts="10.00",
        discount_codes=["SAVE5", "EXTRA5"],
    )

    metrics = compute_discounts(JUNE)

    assert [row.code for row in metrics.codes] == ["EXTRA5 + SAVE5"]
    assert metrics.amount == Decimal("10.00")


def test_digital_downloads_are_hidden_from_the_rate(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(lines=[(product.variants.get(), 1, "20.00")], discounts="2.00", discount_codes=["X"])
    digital = make_order(
        lines=[(product.variants.get(), 1, "0.00")],
        discounts="0.00",
        shipping_charged="0.00",
    )
    digital.lines.update(requires_shipping=False)

    hidden = compute_discounts(JUNE, hide_digital=True)
    shown = compute_discounts(JUNE, hide_digital=False)

    assert hidden.universe == 1
    assert hidden.digital == 1
    assert hidden.rate_pct == Decimal("100.00")
    assert shown.universe == 2
    assert shown.rate_pct == Decimal("50.00")


def test_profit_given_away_is_the_discount_when_costs_are_known(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.00")
    order = make_order(
        lines=[(variant, 1, "24.99")],
        discounts="4.00",
        discount_codes=["SAVE4"],
        shipping_charged="0.00",
        payment_fee="0.50",
    )
    SupplierOrder.objects.create(
        supplier_reference="IT-disc",
        order=order,
        product_cost=Decimal("8.00"),
        shipping_cost=Decimal("0.00"),
    )

    metrics = compute_discounts(JUNE)

    assert metrics.profit_impact == Decimal("4.00")
    assert metrics.codes[0].profit_impact == Decimal("4.00")
    assert metrics.rows[0].profit_impact == Decimal("4.00")


def test_missing_cost_keeps_profit_impact_unknown(make_product, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    make_order(
        lines=[(product.variants.get(), 1, "24.99")],
        discounts="4.00",
        discount_codes=["SAVE4"],
    )

    metrics = compute_discounts(JUNE)

    assert metrics.amount == Decimal("4.00")
    assert metrics.profit_impact is None
    assert metrics.profit_missing == 1
    assert metrics.rows[0].profit_impact is None


def test_codes_rank_by_amount(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        discounts="2.00",
        discount_codes=["SMALL"],
        name="#s",
    )
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        discounts="8.00",
        discount_codes=["BIG"],
        name="#b",
    )

    ranked = rank_codes(compute_discounts(JUNE).codes, sort="amount")

    assert [row.code for row in ranked] == ["BIG", "SMALL"]
