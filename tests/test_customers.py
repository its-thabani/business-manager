"""New vs returning, repeat rate and AOV from paid orders."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.customers import compute_customer_metrics, rank_customers
from apps.core.periods import DateRange
from apps.sales.models import Customer

pytestmark = pytest.mark.django_db

JUNE = DateRange(date(2026, 6, 1), date(2026, 6, 30), "June")


def _buyer(name="Ada Lovelace", email="ada@example.com") -> Customer:
    return Customer.objects.create(first_name=name.split()[0], last_name=name.split()[-1], email=email)


def test_a_first_paid_order_in_the_period_is_new(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    buyer = _buyer()
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=buyer, when=date(2026, 6, 15))

    metrics = compute_customer_metrics(JUNE)

    assert metrics.identified == 1
    assert metrics.new_customers == 1
    assert metrics.returning_customers == 0
    assert metrics.aov == Decimal("38.94")  # 34.99 + 3.95 shipping
    assert metrics.rows[0].is_new


def test_an_earlier_paid_order_makes_them_returning(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    buyer = _buyer()
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=buyer, when=date(2026, 3, 1))
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=buyer, when=date(2026, 6, 15))

    metrics = compute_customer_metrics(JUNE)

    assert metrics.identified == 1
    assert metrics.new_customers == 0
    assert metrics.returning_customers == 1
    assert metrics.repeat_rate_pct == Decimal("100.00")
    assert not metrics.rows[0].is_new


def test_two_orders_in_the_window_are_in_period_repeaters(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    buyer = _buyer()
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        customer=buyer,
        when=date(2026, 6, 2),
        shipping_charged="0.00",
    )
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        customer=buyer,
        when=date(2026, 6, 20),
        shipping_charged="0.00",
    )

    metrics = compute_customer_metrics(JUNE)

    assert metrics.repeaters_in_period == 1
    assert metrics.in_period_repeat_pct == Decimal("100.00")
    assert metrics.aov == Decimal("20.00")


def test_free_downloads_are_excluded_from_aov_by_default(make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    buyer = _buyer()
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=buyer, shipping_charged="0.00")
    make_order(
        lines=[(product.variants.get(), 1, "0.00")],
        customer=buyer,
        shipping_charged="0.00",
        email="ada@example.com",
    )

    hidden = compute_customer_metrics(JUNE, paid_only=True)
    shown = compute_customer_metrics(JUNE, paid_only=False)

    assert hidden.paid_orders == 1
    assert hidden.aov == Decimal("34.99")
    assert shown.paid_orders == 2
    assert shown.aov == Decimal("17.50")


def test_guest_checkouts_without_email_are_unidentified(make_product, make_order):
    product = make_product(variants=[("Black", "L", "10.00")])
    make_order(lines=[(product.variants.get(), 1, "10.00")], shipping_charged="0.00")

    metrics = compute_customer_metrics(JUNE)

    assert metrics.identified == 0
    assert metrics.unidentified_orders == 1
    assert metrics.repeat_rate_pct is None
    assert metrics.aov == Decimal("10.00")


def test_the_same_email_without_a_customer_record_is_one_buyer(make_product, make_order):
    product = make_product(variants=[("Black", "L", "10.00")])
    make_order(
        lines=[(product.variants.get(), 1, "10.00")],
        email="pat@example.com",
        shipping_charged="0.00",
        when=date(2026, 6, 2),
    )
    make_order(
        lines=[(product.variants.get(), 1, "10.00")],
        email="pat@example.com",
        shipping_charged="0.00",
        when=date(2026, 6, 20),
    )

    metrics = compute_customer_metrics(JUNE)

    assert metrics.identified == 1
    assert metrics.rows[0].key == "e:pat@example.com"
    assert metrics.rows[0].period_orders == 2


def test_customers_rank_by_spend(make_product, make_order):
    product = make_product(variants=[("Black", "L", "10.00")])
    low = _buyer("Low Spender", "low@example.com")
    high = _buyer("High Spender", "high@example.com")
    make_order(lines=[(product.variants.get(), 1, "10.00")], customer=low, shipping_charged="0.00")
    make_order(lines=[(product.variants.get(), 3, "10.00")], customer=high, shipping_charged="0.00")

    ranked = rank_customers(compute_customer_metrics(JUNE).rows, sort="spend")

    assert [row.name for row in ranked] == ["High Spender", "Low Spender"]
