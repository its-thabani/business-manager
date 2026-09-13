"""Refund rates follow the sale. Cash is the refund total, not line amounts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.refunds import compute_refunds, rank_refund_items
from apps.core.periods import DateRange
from apps.sales.models import Refund, RefundLine

pytestmark = pytest.mark.django_db

JUNE = DateRange(date(2026, 6, 1), date(2026, 6, 30), "June")


def test_unit_rate_is_returned_over_sold(make_product, make_order, refund_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    variant = product.variants.get()
    make_order(lines=[(variant, 3, "20.00")], shipping_charged="0.00", name="#keep")
    returned = make_order(lines=[(variant, 1, "20.00")], shipping_charged="0.00", name="#back")
    refund_order(returned, amount="20.00")

    metrics = compute_refunds(JUNE)

    assert metrics.sold_units == Decimal("4")
    assert metrics.refunded_units == Decimal("1")
    assert metrics.unit_rate_pct == Decimal("25.00")
    assert metrics.amount == Decimal("20.00")
    assert metrics.refunded_orders == 1
    row = next(item for item in metrics.products if item.product_id == product.pk)
    assert row.rate_pct == Decimal("25.00")


def test_cash_follows_the_refund_total_not_inflated_line_amounts(
    make_product, make_order
):
    product = make_product(variants=[("Black", "L", "30.00"), ("White", "M", "30.00")])
    black, white = product.variants.order_by("position")
    order = make_order(
        lines=[(black, 1, "30.00"), (white, 1, "30.00")],
        shipping_charged="0.00",
    )
    refund = Refund.objects.create(
        order=order,
        refunded_at=order.placed_at,
        refunded_on=order.placed_on,
        amount=Decimal("29.99"),
    )
    RefundLine.objects.create(refund=refund, order_line=order.lines.get(variant=black), quantity=1, amount=Decimal("36.99"))
    RefundLine.objects.create(refund=refund, order_line=order.lines.get(variant=white), quantity=1, amount=Decimal("29.99"))

    metrics = compute_refunds(JUNE)
    row = next(item for item in metrics.products if item.product_id == product.pk)

    assert metrics.amount == Decimal("29.99")
    assert row.amount == Decimal("29.99")
    assert row.refunded_units == Decimal("2")


def test_a_zero_pound_return_still_counts_units(make_product, make_order, refund_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    order = make_order(lines=[(product.variants.get(), 1, "34.99")], shipping_charged="0.00")
    refund_order(order, amount="0.00")

    metrics = compute_refunds(JUNE)

    assert metrics.amount == Decimal("0.00")
    assert metrics.refunded_units == Decimal("1")
    assert metrics.unit_rate_pct == Decimal("100.00")


def test_refund_without_lines_is_unallocated_cash(make_product, make_order):
    product = make_product(variants=[("Black", "L", "33.99")])
    order = make_order(lines=[(product.variants.get(), 1, "33.99")], shipping_charged="0.00")
    Refund.objects.create(
        order=order,
        refunded_at=order.placed_at,
        refunded_on=order.placed_on,
        amount=Decimal("33.99"),
    )

    metrics = compute_refunds(JUNE)

    assert metrics.unallocated == Decimal("33.99")
    assert any(row.label == "Unallocated" and row.amount == Decimal("33.99") for row in metrics.products)


def test_digital_downloads_are_hidden_from_the_rate(make_product, make_order, refund_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    paid = make_order(lines=[(product.variants.get(), 1, "20.00")], shipping_charged="0.00")
    refund_order(paid, amount="20.00")
    digital = make_order(
        lines=[(product.variants.get(), 1, "0.00")],
        shipping_charged="0.00",
        name="#digital",
    )
    digital.lines.update(requires_shipping=False)

    hidden = compute_refunds(JUNE, hide_digital=True)
    shown = compute_refunds(JUNE, hide_digital=False)

    assert hidden.universe == 1
    assert hidden.digital == 1
    assert hidden.order_rate_pct == Decimal("100.00")
    assert shown.universe == 2
    assert shown.order_rate_pct == Decimal("50.00")


def test_products_rank_by_rate(make_product, make_order, refund_order):
    quiet = make_product(title="Quiet Tee", variants=[("Black", "L", "20.00")])
    noisy = make_product(title="Noisy Hoodie", variants=[("Black", "L", "20.00")])
    make_order(lines=[(quiet.variants.get(), 4, "20.00")], shipping_charged="0.00", name="#q")
    make_order(lines=[(noisy.variants.get(), 1, "20.00")], shipping_charged="0.00", name="#n1")
    back = make_order(lines=[(noisy.variants.get(), 1, "20.00")], shipping_charged="0.00", name="#n2")
    refund_order(back, amount="20.00")

    ranked = rank_refund_items(compute_refunds(JUNE).products, sort="rate")

    assert [row.label for row in ranked if row.refunded_units][0] == "Noisy Hoodie"
