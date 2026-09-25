"""What-if simulations replay history and never write the books."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.profitability import compute_order_profits
from apps.analytics.simulations import SimulationSpec, simulate
from apps.core.periods import DateRange
from apps.web.charts import line_chart

pytestmark = pytest.mark.django_db


def _range() -> DateRange:
    return DateRange(date(2026, 1, 1), date(2026, 12, 31), "2026")


def test_price_rise_increases_revenue_and_profit(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    map_variant(product.variants.get(), cost="8.00")
    make_order(
        lines=[(product.variants.get(), 2, "20.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
    )

    result = simulate(
        compute_order_profits(_range()),
        SimulationSpec(price_change=Decimal("2.00")),
        date_range=_range(),
    )

    assert result.baseline.net_revenue == Decimal("40.00")
    assert result.projected.net_revenue == Decimal("44.00")
    assert result.profit_delta == Decimal("4.00")
    assert "Selling price +£2.00 per unit" in result.assumptions


def test_missing_cost_stays_unknown_unless_the_scenario_supplies_one(make_product, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
    )
    profits = compute_order_profits(_range())

    unknown = simulate(profits, SimulationSpec(price_change=Decimal("1.00")), date_range=_range())
    assert unknown.profit_delta is None
    assert unknown.projected.is_complete is False

    filled = simulate(profits, SimulationSpec(new_unit_cost=Decimal("7.00")), date_range=_range())
    assert filled.projected.is_complete is True
    assert filled.projected.profit == Decimal("11.60")
    assert any("assumption" in note.lower() for note in filled.notes)


def test_free_shipping_forgoes_postage_charged(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    map_variant(product.variants.get(), cost="8.00")
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="3.95",
        payment_fee="0.00",
    )

    result = simulate(
        compute_order_profits(_range()),
        SimulationSpec(free_shipping=True),
        date_range=_range(),
    )

    assert result.baseline.net_revenue == Decimal("23.95")
    assert result.projected.net_revenue == Decimal("20.00")
    assert result.break_even_volume_pct is not None


def test_simulation_does_not_write_orders(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "20.00")])
    map_variant(product.variants.get(), cost="8.00")
    order = make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
    )

    simulate(
        compute_order_profits(_range()),
        SimulationSpec(new_price=Decimal("99.00")),
        date_range=_range(),
    )

    order.refresh_from_db()
    assert order.lines.get().unit_price == Decimal("20.00")


def test_simulate_page_renders(client, make_product, map_variant, make_order):
    product = make_product(title="Classic Tee", variants=[("Black", "L", "20.00")])
    map_variant(product.variants.get(), cost="8.00")
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
    )

    response = client.get("/simulate/?range=all&price_change=2")

    assert response.status_code == 200
    assert b"Assumptions" in response.content
    assert b"only the boxes" in response.content
    assert b"Classic Tee" in response.content
    assert b"Printer VAT" in response.content


def test_new_product_quote_is_on_the_simulate_page(client):
    response = client.get(
        "/simulate/?range=all&quote=1&quote_name=Jacket&printer_product=18.50&printer_postage=3.15&markup=40&customer_postage=4.99"
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "Sell Jacket for" in html
    assert "You pay Inkthreadable" in html
    assert "free shipping" in html
    assert "Price range" in html


def test_line_chart_places_a_higher_point_higher_on_the_page():
    chart = line_chart(
        [
            {"label": "Jan", "profit": Decimal("10.00")},
            {"label": "Feb", "profit": Decimal("40.00")},
        ],
        ["profit"],
    )
    jan, feb = chart["series"]["profit"]["points"]
    assert feb["y"] < jan["y"]


def test_monzo_upload_skips_a_row_already_held(client, tmp_path):
    from apps.finance.models import BankAccount, BankTransaction, TransactionSource

    account, _ = BankAccount.objects.get_or_create(
        name="Monzo Business",
        defaults={"institution": "Monzo", "currency": "GBP", "is_primary": True},
    )
    BankTransaction.objects.create(
        account=account,
        source=TransactionSource.MONZO_CSV,
        external_id="tx_already",
        occurred_on=date(2026, 8, 1),
        counterparty="Stripe",
        amount=Decimal("12.00"),
    )
    path = tmp_path / "October.csv"
    path.write_text(
        "Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,"
        "Local amount,Local currency,Notes and #tags,Address,Receipt,Description,"
        "Category split,Money Out,Money In,Balance,Balance currency\n"
        "tx_already,01/08/2026,12:00:00,Faster payment,Stripe,,,12.00,GBP,"
        "12.00,GBP,,,,already,,,,,,,,\n"
        "tx_new_oct,03/10/2026,12:00:00,Faster payment,Stripe,,,15.00,GBP,"
        "15.00,GBP,,,,october,,,,,,,,\n",
        encoding="utf-8",
    )

    with path.open("rb") as handle:
        response = client.post("/integrations/", {"monzo_csv": handle})

    assert response.status_code == 302
    from apps.finance.models import BankTransaction

    assert BankTransaction.objects.filter(external_id="tx_already").count() == 1
    assert BankTransaction.objects.filter(external_id="tx_new_oct").exists()
