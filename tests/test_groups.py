"""Title-based product groups. Shopify types are empty on this shop."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.groups import ensure_default_groups
from apps.analytics.profitability import compute_order_profits, group_performance
from apps.catalog.models import ProductGroup
from apps.core.money import quantise
from apps.core.periods import DateRange

pytestmark = pytest.mark.django_db

JUNE = DateRange(date(2026, 6, 1), date(2026, 6, 30), "June")


def test_titles_fill_groups_when_shopify_type_is_missing(make_product):
    make_product(title="Jesus Is My Lifeline Hoodie", product_type="")
    make_product(title="Perfect Peace Graphic T-Shirt", product_type="")
    make_product(title="10 Bible Verses for Hope", product_type="")

    ensure_default_groups()

    hoodies = ProductGroup.objects.get(slug="hoodies")
    tees = ProductGroup.objects.get(slug="t-shirts")
    digital = ProductGroup.objects.get(slug="digital-downloads")
    assert hoodies.products.filter(title__icontains="Hoodie").exists()
    assert tees.products.filter(title__icontains="T-Shirt").exists()
    assert digital.products.filter(title__icontains="Bible Verses").exists()
    merch = make_product(title="Personalised Bible Verse T-shirt", product_type="")
    ensure_default_groups()
    assert merch not in ProductGroup.objects.get(slug="digital-downloads").products.all()
    assert merch in ProductGroup.objects.get(slug="t-shirts").products.all()
    jumper = make_product(title="Way Truth Life Sweatshirt", product_type="")
    ensure_default_groups()
    assert jumper in ProductGroup.objects.get(slug="sweatshirts").products.all()
    assert jumper not in ProductGroup.objects.get(slug="t-shirts").products.all()


def test_group_performance_adds_member_sales(make_product, make_order):
    hoodie = make_product(title="Ask Me About Jesus Hoodie", product_type="")
    tee = make_product(title="Beloved Please T-Shirt", product_type="")
    make_order(lines=[(hoodie.variants.get(), 1, "34.99")], shipping_charged="0.00")
    make_order(lines=[(tee.variants.get(), 2, "24.99")], shipping_charged="0.00")
    ensure_default_groups()

    hoodies = ProductGroup.objects.get(slug="hoodies")
    row = group_performance(compute_order_profits(JUNE), hoodies, date_range=JUNE)

    assert row.units == 1
    assert row.net_revenue == Decimal("34.99")
    assert row.group_id == hoodies.pk


def test_group_estimates_profit_from_the_lines_that_have_a_cost(
    make_product, map_variant, make_order
):
    known = make_product(title="Ask Me About Jesus Hoodie", product_type="")
    map_variant(known.variants.get(), cost="10.00")
    make_order(
        lines=[(known.variants.get(), 1, "40.00")],
        shipping_charged="0.00",
        payment_fee="1.00",
    )
    unknown = make_product(title="Other Faith Hoodie", product_type="")
    make_order(
        lines=[(unknown.variants.get(), 1, "40.00")],
        shipping_charged="0.00",
        payment_fee="1.00",
    )
    ensure_default_groups()
    hoodies = ProductGroup.objects.get(slug="hoodies")

    row = group_performance(compute_order_profits(JUNE), hoodies, date_range=JUNE)

    assert row.units == 2
    assert not row.is_complete
    assert row.known_units == 1
    assert row.is_estimate
    rate = row.known_profit / row.known_net_revenue
    assert row.estimated_profit == quantise(row.net_revenue * rate)
