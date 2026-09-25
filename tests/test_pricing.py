"""A new garment is priced from the printer quote, not from past orders."""

from decimal import Decimal

import pytest

from apps.analytics.pricing import quote_new_product, shelf_price

pytestmark = pytest.mark.django_db


def test_shelf_price_rounds_up_to_the_next_99():
    assert shelf_price(Decimal("33.57")) == Decimal("33.99")
    assert shelf_price(Decimal("33.99")) == Decimal("33.99")
    assert shelf_price(Decimal("34.00")) == Decimal("34.99")


def test_forty_percent_markup_includes_postage_and_vat():
    quote = quote_new_product(
        name="Jacket",
        product_net=Decimal("16.83"),
        postage_net=Decimal("3.15"),
        markup_pct=Decimal("40"),
        customer_postage=Decimal("4.99"),
    )

    assert quote.vat == Decimal("4.00")
    assert quote.landed == Decimal("23.98")
    recommended = quote.recommended
    assert recommended.exact_price == Decimal("33.57")
    assert recommended.shelf_price == Decimal("33.99")
    assert recommended.garment_if_postage_charged == Decimal("29.00")
    assert recommended.kept_if_priced_low_and_free < recommended.kept
    assert [row.markup_pct for row in quote.rows] == [
        Decimal("30.00"),
        Decimal("40.00"),
        Decimal("50.00"),
    ]


def test_free_shipping_price_is_the_full_marked_up_bill():
    quote = quote_new_product(
        name="Jacket",
        product_net=Decimal("20.00"),
        postage_net=Decimal("0.00"),
        markup_pct=Decimal("40"),
        customer_postage=Decimal("0"),
    )

    assert quote.landed == Decimal("24.00")
    assert quote.recommended.exact_price == Decimal("33.60")
    assert quote.recommended.garment_if_postage_charged == quote.recommended.shelf_price
