"""Tests for order and product profitability.

The emphasis here is on the honesty of the output rather than only its
arithmetic. A profit engine that returns a plausible number when a cost is
missing is actively dangerous, so a good share of these tests assert that the
engine refuses to answer, or flags itself, rather than that it produces a value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.profitability import (
    Basis,
    CostResolver,
    all_product_performance,
    attach_revenue_share,
    compute_order_profit,
    compute_order_profits,
    filter_product_rows,
    group_performance,
    option_mix,
    product_performance,
    rank_products,
    summarise,
    trading_by_month,
    unmapped_variant_report,
    variant_performance,
)
from apps.catalog.models import ProductGroup, normalise_size
from apps.core.money import quantise
from apps.core.periods import DateRange
from apps.sales.models import Order
from apps.supplier.models import SupplierOrder

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Order-level profitability
# ---------------------------------------------------------------------------


def test_a_simple_order_breaks_down_into_revenue_costs_and_profit(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")], shipping_charged="3.95")
    SupplierOrder.objects.create(
        supplier_reference="IT-1",
        order=order,
        product_cost=Decimal("8.10"),
        shipping_cost=Decimal("2.95"),
    )

    profit = compute_order_profit(order)

    assert profit.product_revenue == Decimal("24.99")
    assert profit.shipping_charged == Decimal("3.95")
    assert profit.net_sales == Decimal("28.94")
    assert profit.supplier_product_cost == Decimal("8.10")
    assert profit.supplier_shipping_cost == Decimal("2.95")
    # Fee is estimated at 1.5% of £28.94 plus 20p.
    assert profit.payment_fee == Decimal("0.63")
    assert profit.payment_fee_basis == Basis.ESTIMATED
    assert profit.supplier_tax == Decimal("2.21")
    assert profit.supplier_tax_basis == Basis.ESTIMATED
    assert profit.total_costs == Decimal("13.89")
    assert profit.contribution_profit == Decimal("15.05")
    assert profit.margin_pct == Decimal("52.00")
    assert profit.is_complete
    assert profit.supplier_invoice_total == Decimal("13.26")


def test_printer_vat_is_included_in_contribution(make_product, make_order):
    """Laura's invoice VAT is a real cost: the business does not reclaim it."""
    product = make_product(variants=[("Dusty Pink", "L", "34.99")])
    order = make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        shipping_charged="4.99",
        discounts="3.49",
        payment_fee="0.98",
    )
    SupplierOrder.objects.create(
        supplier_reference="2136792",
        order=order,
        product_cost=Decimal("16.83"),
        shipping_cost=Decimal("3.15"),
        tax=Decimal("3.99"),
        total_cost=Decimal("23.97"),
    )

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost == Decimal("16.83")
    assert profit.supplier_shipping_cost == Decimal("3.15")
    assert profit.supplier_tax == Decimal("3.99")
    assert profit.supplier_invoice_total == Decimal("23.97")
    assert profit.payment_fee == Decimal("0.98")
    assert profit.supplier_tax_basis == Basis.ACTUAL
    assert profit.total_costs == Decimal("24.95")
    assert profit.contribution_profit == Decimal("11.54")


def test_split_fulfilments_are_summed_not_first_only(make_product, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    order = make_order(lines=[(product.variants.get(), 2, "24.99")], shipping_charged="3.95")
    SupplierOrder.objects.create(
        supplier_reference="IT-a",
        order=order,
        product_cost=Decimal("8.10"),
        shipping_cost=Decimal("2.95"),
    )
    SupplierOrder.objects.create(
        supplier_reference="IT-b",
        order=order,
        product_cost=Decimal("8.10"),
        shipping_cost=Decimal("2.95"),
    )

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost == Decimal("16.20")
    assert profit.supplier_shipping_cost == Decimal("5.90")
    assert profit.supplier_product_cost_basis == Basis.ACTUAL


def test_profit_is_none_when_a_supplier_cost_is_unknown(make_product, make_order):
    """The whole point: an unmapped variant must not silently become free."""
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    order = make_order(lines=[(variant, 1, "24.99")])

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost is None
    assert profit.supplier_product_cost_basis == Basis.MISSING
    assert profit.contribution_profit is None
    assert profit.margin_pct is None
    assert not profit.is_complete
    assert "supplier product cost" in profit.missing_cost_labels
    assert any("no supplier cost" in w for w in profit.warnings)


def test_the_warning_names_the_product_that_needs_mapping(make_product, make_order):
    product = make_product(title="Mystery Hoodie", variants=[("Grey", "M", "42.00")])
    order = make_order(lines=[(product.variants.get(), 1, "42.00")])

    profit = compute_order_profit(order)

    assert any("Mystery Hoodie" in w for w in profit.warnings)


def test_a_provisional_profit_is_still_available_for_ranking(make_product, make_order):
    """Aggregates need a number even when incomplete, clearly labelled as such."""
    product = make_product(variants=[("Black", "L", "20.00")])
    order = make_order(lines=[(product.variants.get(), 1, "20.00")], shipping_charged="0.00")

    profit = compute_order_profit(order)

    assert profit.contribution_profit is None
    # Revenue less the fee, which is all that is known.
    assert profit.provisional_contribution_profit == Decimal("19.50")


def test_an_actual_supplier_charge_beats_the_price_list(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")])
    SupplierOrder.objects.create(
        supplier_reference="IT-2",
        order=order,
        product_cost=Decimal("9.40"),
        shipping_cost=Decimal("2.95"),
    )

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost == Decimal("9.40")
    assert profit.supplier_product_cost_basis == Basis.ACTUAL
    assert any("price list may be out of date" in w for w in profit.warnings)


def test_no_warning_when_the_actual_charge_matches_the_price_list(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")])
    SupplierOrder.objects.create(
        supplier_reference="IT-3",
        order=order,
        product_cost=Decimal("8.10"),
        shipping_cost=Decimal("2.95"),
    )

    assert compute_order_profit(order).warnings == []


def test_an_actual_charge_is_spread_across_lines_so_the_parts_match_the_whole(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99"), ("White", "S", "24.99")])
    black, white = product.variants.order_by("position")
    map_variant(black, cost="8.00")
    map_variant(white, cost="8.00")
    order = make_order(lines=[(black, 1, "24.99"), (white, 1, "24.99")])
    SupplierOrder.objects.create(
        supplier_reference="IT-4",
        order=order,
        product_cost=Decimal("18.00"),
        shipping_cost=Decimal("3.50"),
    )

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost == Decimal("18.00")
    assert sum(line.product_cost for line in profit.lines) == Decimal("18.00")
    assert all(line.product_cost == Decimal("9.00") for line in profit.lines)


def test_a_recorded_payment_fee_is_used_instead_of_the_estimate(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")], payment_fee="0.71")

    profit = compute_order_profit(order)

    assert profit.payment_fee == Decimal("0.71")
    assert profit.payment_fee_basis == Basis.ACTUAL


def test_a_line_cost_override_counts_as_actual(make_product, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    order = make_order(lines=[(variant, 1, "24.99")])
    line = order.lines.get()
    line.unit_cost_override = Decimal("7.25")
    line.save()

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost == Decimal("7.25")
    assert profit.supplier_product_cost_basis == Basis.ACTUAL


def test_a_mapping_cost_override_beats_the_price_list(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10", cost_override="6.50")
    order = make_order(lines=[(variant, 1, "24.99")])

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost == Decimal("6.50")
    assert profit.supplier_product_cost_basis == Basis.ACTUAL


def test_discounts_reduce_net_sales(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(
        lines=[(variant, 2, "24.99")], discounts="7.50", shipping_charged="0.00"
    )

    profit = compute_order_profit(order)

    assert profit.product_revenue == Decimal("49.98")
    assert profit.discounts == Decimal("7.50")
    assert profit.net_sales == Decimal("42.48")


def test_a_mismatch_between_order_and_line_discounts_is_flagged(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")])
    order.total_discounts = Decimal("5.00")  # not allocated to any line
    order.save()

    profit = compute_order_profit(order)

    assert any("does not match" in w for w in profit.warnings)


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


def test_a_refund_reduces_revenue_but_not_the_supplier_cost(
    make_product, map_variant, make_order, refund_order
):
    """Print-on-demand has no restocking: a refund costs the sale and the shirt."""
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")], shipping_charged="0.00")
    refund_order(order, amount="24.99")

    profit = compute_order_profit(order)

    assert profit.refunds == Decimal("24.99")
    assert profit.net_sales == Decimal("0.00")
    assert profit.supplier_product_cost == Decimal("8.10")
    # Postage is unknown (no linked Inkthreadable order), so a complete profit
    # figure is refused. The provisional figure still shows the garment and fee
    # as a loss — POD does not get the shirt back.
    assert profit.contribution_profit is None
    assert "supplier shipping cost" in profit.missing_cost_labels
    assert profit.provisional_contribution_profit < Decimal("0.00")


def test_a_partial_refund_reduces_only_the_refunded_line(
    make_product, map_variant, make_order, refund_order
):
    product = make_product(variants=[("Black", "L", "20.00"), ("White", "S", "20.00")])
    black, white = product.variants.order_by("position")
    map_variant(black, cost="8.00")
    map_variant(white, cost="8.00")
    order = make_order(
        lines=[(black, 1, "20.00"), (white, 1, "20.00")], shipping_charged="0.00"
    )
    refund_order(order, line=order.lines.last(), amount="20.00")

    profit = compute_order_profit(order)
    by_variant = {lp.line.variant_id: lp for lp in profit.lines}

    assert by_variant[black.pk].net_revenue == Decimal("20.00")
    assert by_variant[white.pk].net_revenue == Decimal("0.00")
    assert by_variant[white.pk].refunded_quantity == Decimal("1")


# ---------------------------------------------------------------------------
# Effective-dated costs
# ---------------------------------------------------------------------------


def test_an_order_is_costed_at_the_price_that_applied_on_the_day(
    make_product, map_variant, make_order
):
    """A supplier price rise must not retroactively rewrite last year's margins."""
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(
        variant,
        costs=[(date(2025, 1, 1), "8.10"), (date(2026, 6, 1), "9.35")],
    )

    before = make_order(lines=[(variant, 1, "24.99")], when=date(2026, 5, 20))
    after = make_order(lines=[(variant, 1, "24.99")], when=date(2026, 6, 20))

    assert compute_order_profit(before).supplier_product_cost == Decimal("8.10")
    assert compute_order_profit(after).supplier_product_cost == Decimal("9.35")


def test_a_cost_that_starts_after_the_order_is_treated_as_unknown(
    make_product, map_variant, make_order
):
    """Better to admit ignorance than to cost an old order at a new price."""
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, costs=[(date(2026, 6, 1), "9.35")])
    order = make_order(lines=[(variant, 1, "24.99")], when=date(2026, 1, 10))

    profit = compute_order_profit(order)

    assert profit.supplier_product_cost is None
    assert profit.contribution_profit is None


def test_a_mapping_with_no_costs_at_all_yields_no_cost(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost=None)
    order = make_order(lines=[(variant, 1, "24.99")])

    assert compute_order_profit(order).supplier_product_cost is None


def test_the_resolver_reports_the_basis_it_used(make_product, map_variant):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")

    resolver = CostResolver([variant.pk])
    cost, basis = resolver.unit_cost(variant.pk, date(2026, 6, 15))

    assert cost == Decimal("8.10")
    assert basis == Basis.PRICE_LIST
    assert resolver.unit_cost(None, date(2026, 6, 15)) == (None, Basis.MISSING)


def test_shopify_cost_is_used_only_when_no_supplier_cost_exists(make_product):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    variant.shopify_unit_cost = Decimal("6.50")
    variant.save(update_fields=["shopify_unit_cost"])

    cost, basis = CostResolver([variant.pk]).unit_cost(variant.pk, date(2026, 6, 15))

    assert cost == Decimal("6.50")
    assert basis == Basis.SHOPIFY


def test_a_supplier_mapping_beats_the_shopify_cost(make_product, map_variant):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    variant.shopify_unit_cost = Decimal("6.50")
    variant.save(update_fields=["shopify_unit_cost"])
    map_variant(variant, cost="8.10")

    cost, basis = CostResolver([variant.pk]).unit_cost(variant.pk, date(2026, 6, 15))

    assert cost == Decimal("8.10")
    assert basis == Basis.PRICE_LIST


# ---------------------------------------------------------------------------
# Allocation of order-level costs
# ---------------------------------------------------------------------------


def test_postage_is_shared_between_lines_in_proportion_to_revenue(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "30.00"), ("White", "S", "10.00")])
    expensive, cheap = product.variants.order_by("position")
    map_variant(expensive, cost="9.00")
    map_variant(cheap, cost="4.00")
    order = make_order(
        lines=[(expensive, 1, "30.00"), (cheap, 1, "10.00")], shipping_charged="0.00"
    )
    SupplierOrder.objects.create(
        supplier_reference="IT-5",
        order=order,
        product_cost=Decimal("13.00"),
        shipping_cost=Decimal("4.00"),
    )

    profit = compute_order_profit(order)
    by_variant = {lp.line.variant_id: lp for lp in profit.lines}

    # £30 of £40 is 75% of the revenue, so it carries 75% of the £4 postage
    # and of the £3.40 printer VAT (20% of £17 net).
    assert by_variant[expensive.pk].allocated_shipping_cost == Decimal("3.00")
    assert by_variant[cheap.pk].allocated_shipping_cost == Decimal("1.00")
    assert by_variant[expensive.pk].allocated_tax == Decimal("2.55")
    assert by_variant[cheap.pk].allocated_tax == Decimal("0.85")


def test_costs_are_shared_by_quantity_when_an_order_is_entirely_discounted(
    make_product, map_variant, make_order
):
    """A fully discounted order has no revenue to weight by; it must not divide by zero."""
    product = make_product(variants=[("Black", "L", "20.00"), ("White", "S", "20.00")])
    black, white = product.variants.order_by("position")
    map_variant(black, cost="8.00")
    map_variant(white, cost="8.00")
    order = make_order(
        lines=[(black, 1, "20.00"), (white, 1, "20.00")],
        discounts="40.00",
        shipping_charged="0.00",
    )
    SupplierOrder.objects.create(
        supplier_reference="IT-6",
        order=order,
        product_cost=Decimal("16.00"),
        shipping_cost=Decimal("4.00"),
    )

    profit = compute_order_profit(order)

    assert profit.net_sales == Decimal("0.00")
    assert all(lp.allocated_shipping_cost == Decimal("2.00") for lp in profit.lines)


def test_postage_is_not_applicable_when_nothing_needs_shipping(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "5.00")])
    variant = product.variants.get()
    map_variant(variant, cost="1.00")
    order = make_order(lines=[(variant, 1, "5.00")], shipping_charged="0.00")
    order.lines.update(requires_shipping=False)

    profit = compute_order_profit(order)

    assert profit.supplier_shipping_cost == Decimal("0.00")
    assert profit.supplier_shipping_cost_basis == Basis.NOT_APPLICABLE
    assert profit.is_complete


# ---------------------------------------------------------------------------
# Tax treatment
# ---------------------------------------------------------------------------


def test_checkout_tax_stays_in_sales_because_the_business_is_not_vat_registered(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "20.00")])
    variant = product.variants.get()
    map_variant(variant, cost="8.00")
    order = make_order(lines=[(variant, 1, "20.00")], tax="4.00", shipping_charged="0.00")

    assert compute_order_profit(order).net_sales == Decimal("20.00")


def test_checkout_tax_can_be_held_for_hmrc_if_the_business_registers(
    settings, make_product, map_variant, make_order
):
    settings.PROFIT_ASSUMPTIONS = {
        "payment_fee_percent": Decimal("1.5"),
        "payment_fee_fixed": Decimal("0.20"),
        "tax_treatment": "exclude",
        "supplier_vat_rate": Decimal("20"),
    }
    product = make_product(variants=[("Black", "L", "20.00")])
    variant = product.variants.get()
    map_variant(variant, cost="8.00")
    order = make_order(lines=[(variant, 1, "20.00")], tax="4.00", shipping_charged="0.00")

    assert compute_order_profit(order).net_sales == Decimal("16.00")


# ---------------------------------------------------------------------------
# Which orders count
# ---------------------------------------------------------------------------


def test_test_and_cancelled_orders_are_left_out_of_the_figures(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    make_order(lines=[(variant, 1, "24.99")])
    make_order(lines=[(variant, 1, "24.99")], is_test=True)
    make_order(lines=[(variant, 1, "24.99")], cancelled=True)

    assert Order.objects.count() == 3
    assert Order.objects.countable().count() == 1
    assert len(compute_order_profits()) == 1


def test_orders_are_filtered_by_the_local_calendar_day(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    make_order(lines=[(variant, 1, "24.99")], when=date(2026, 5, 31))
    make_order(lines=[(variant, 1, "24.99")], when=date(2026, 6, 1))
    make_order(lines=[(variant, 1, "24.99")], when=date(2026, 6, 30))
    make_order(lines=[(variant, 1, "24.99")], when=date(2026, 7, 1))

    june = DateRange(date(2026, 6, 1), date(2026, 6, 30), label="June")

    assert len(compute_order_profits(june)) == 2


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_product_performance_aggregates_across_orders(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    for _ in range(3):
        order = make_order(lines=[(variant, 2, "24.99")], shipping_charged="0.00")
        SupplierOrder.objects.create(
            supplier_reference=f"IT-agg-{order.pk}",
            order=order,
            product_cost=Decimal("16.20"),
            shipping_cost=Decimal("3.00"),
        )

    profits = compute_order_profits()
    performance = product_performance(profits, product)

    assert performance.orders == 3
    assert performance.units == 6
    assert performance.gross_revenue == Decimal("149.94")
    assert performance.supplier_cost == Decimal("48.60")
    assert performance.shipping_cost == Decimal("9.00")
    assert performance.is_complete
    assert performance.avg_selling_price == Decimal("24.99")
    assert performance.avg_profit_per_order == performance.profit / 3


def test_an_aggregate_reports_how_many_lines_lack_a_cost(make_product, map_variant, make_order):
    """Aggregates must own up to being built on incomplete data."""
    product = make_product(variants=[("Black", "L", "24.99"), ("White", "S", "24.99")])
    mapped, unmapped = product.variants.order_by("position")
    map_variant(mapped, cost="8.10")
    make_order(lines=[(mapped, 1, "24.99"), (unmapped, 1, "24.99")])

    performance = product_performance(compute_order_profits(), product)

    assert performance.lines_total == 2
    assert performance.lines_missing_cost == 1
    assert not performance.is_complete
    assert performance.completeness_pct == Decimal("50.00")
    assert performance.known_units == 1
    assert performance.known_margin_pct is not None
    assert performance.is_estimate
    rate = performance.known_profit / performance.known_net_revenue
    assert performance.estimated_profit == quantise(performance.net_revenue * rate)
    assert performance.displayed_profit == performance.estimated_profit
    assert performance.profit_basis == "estimate"


def test_no_estimate_when_nothing_sold_has_a_cost(make_product, make_order):
    product = make_product(variants=[("Black", "L", "24.99")])
    make_order(lines=[(product.variants.get(), 1, "24.99")])

    performance = product_performance(compute_order_profits(), product)

    assert not performance.is_complete
    assert not performance.is_estimate
    assert performance.estimated_profit is None
    assert performance.displayed_profit is None
    assert performance.profit_basis == ""


def test_group_performance_covers_every_product_in_the_group(
    make_product, map_variant, make_order
):
    tee = make_product(title="Classic Tee", variants=[("Black", "L", "24.99")])
    hoodie = make_product(
        title="Heavy Hoodie", product_type="Hoodie", variants=[("Grey", "M", "42.00")]
    )
    map_variant(tee.variants.get(), cost="8.10")
    map_variant(hoodie.variants.get(), cost="18.00")

    group = ProductGroup.objects.create(name="Tops", slug="tops")
    group.products.add(tee, hoodie)

    make_order(lines=[(tee.variants.get(), 1, "24.99")])
    make_order(lines=[(hoodie.variants.get(), 1, "42.00")])

    performance = group_performance(compute_order_profits(), group)

    assert performance.orders == 2
    assert performance.units == 2
    assert performance.gross_revenue == Decimal("66.99")


def test_variants_are_ranked_by_margin_with_the_best_first(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "30.00"), ("White", "S", "12.00")])
    good, poor = product.variants.order_by("position")
    map_variant(good, cost="8.00")
    map_variant(poor, cost="8.00")
    make_order(lines=[(good, 1, "30.00")], shipping_charged="0.00")
    make_order(lines=[(poor, 1, "12.00")], shipping_charged="0.00")

    ranked = variant_performance(compute_order_profits(), product=product)

    assert [p.label for p in ranked] == ["Black / L", "White / S"]
    assert ranked[0].margin_pct > ranked[1].margin_pct


def test_variants_without_a_known_cost_rank_last(make_product, map_variant, make_order):
    product = make_product(variants=[("Black", "L", "30.00"), ("White", "S", "30.00")])
    mapped, unmapped = product.variants.order_by("position")
    map_variant(mapped, cost="8.00")
    make_order(lines=[(mapped, 1, "30.00"), (unmapped, 1, "30.00")])

    ranked = variant_performance(compute_order_profits(), product=product)

    assert ranked[-1].label == "White / S"
    assert ranked[-1].lines_missing_cost == 1


def test_size_mix_reports_share_of_units_in_wearable_order(
    make_product, map_variant, make_order
):
    product = make_product(
        variants=[("Black", "S", "24.99"), ("Black", "L", "24.99"), ("Black", "XL", "24.99")]
    )
    small, large, xlarge = product.variants.order_by("position")
    for variant in (small, large, xlarge):
        map_variant(variant, cost="8.10")
    make_order(lines=[(large, 3, "24.99")])
    make_order(lines=[(small, 1, "24.99"), (xlarge, 1, "24.99")])

    mix = option_mix(compute_order_profits(), product, attribute="size")

    assert [row["value"] for row in mix] == ["S", "L", "XL"]
    by_size = {row["value"]: row for row in mix}
    assert by_size["L"]["units"] == 3
    assert by_size["L"]["share_pct"] == Decimal("60.00")


def test_colour_mix_is_ordered_by_popularity(make_product, map_variant, make_order):
    product = make_product(
        variants=[("Black", "L", "24.99"), ("White", "L", "24.99")]
    )
    black, white = product.variants.order_by("position")
    map_variant(black, cost="8.10")
    map_variant(white, cost="8.10")
    make_order(lines=[(black, 4, "24.99"), (white, 1, "24.99")])

    mix = option_mix(compute_order_profits(), product, attribute="colour")

    assert [row["value"] for row in mix] == ["Black", "White"]
    assert mix[0]["share_pct"] == Decimal("80.00")


def test_option_mix_margin_is_withheld_when_a_cost_is_missing(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    make_order(lines=[(variant, 1, "24.99")])

    mix = option_mix(compute_order_profits(), product, attribute="size")

    assert mix[0]["margin_pct"] is None


def test_summarise_over_everything_matches_the_sum_of_its_products(
    make_product, map_variant, make_order
):
    tee = make_product(title="Tee", variants=[("Black", "L", "24.99")])
    cap = make_product(title="Cap", product_type="Hat", variants=[("Black", "One", "14.99")])
    map_variant(tee.variants.get(), cost="8.10")
    map_variant(cap.variants.get(), cost="5.00")
    make_order(lines=[(tee.variants.get(), 1, "24.99")])
    make_order(lines=[(cap.variants.get(), 2, "14.99")])

    profits = compute_order_profits()
    whole = summarise(profits, label="Everything")

    assert whole.orders == 2
    assert whole.units == 3
    assert whole.gross_revenue == (
        product_performance(profits, tee).gross_revenue
        + product_performance(profits, cap).gross_revenue
    )


def test_an_empty_period_reports_zero_rather_than_failing():
    performance = summarise([], label="Nothing")

    assert performance.orders == 0
    assert performance.units == 0
    assert performance.profit == Decimal("0.00")
    assert performance.margin_pct is None
    assert performance.avg_order_value is None
    assert performance.completeness_pct is None


def test_all_products_are_ranked_by_units_sold(make_product, map_variant, make_order):
    hoodie = make_product(title="Hoodie", variants=[("Black", "L", "34.99")])
    tee = make_product(title="Tee", product_type="T-Shirt", variants=[("Black", "L", "24.99")])
    map_variant(hoodie.variants.get(), cost="12.00")
    map_variant(tee.variants.get(), cost="8.00")
    make_order(lines=[(hoodie.variants.get(), 1, "34.99")])
    make_order(lines=[(tee.variants.get(), 5, "24.99")])

    ranked = all_product_performance(compute_order_profits())

    assert [row.label for row in ranked] == ["Tee", "Hoodie"]
    assert ranked[0].product_id == tee.pk
    assert ranked[0].units == 5


def test_profit_ranking_puts_incomplete_products_last(make_product, map_variant, make_order):
    mapped = make_product(title="Mapped", variants=[("Black", "L", "20.00")])
    mystery = make_product(title="Mystery", variants=[("Black", "L", "80.00")])
    map_variant(mapped.variants.get(), cost="8.00")
    make_order(lines=[(mapped.variants.get(), 1, "20.00")], shipping_charged="0.00")
    make_order(lines=[(mystery.variants.get(), 1, "80.00")], shipping_charged="0.00")

    ranked = rank_products(all_product_performance(compute_order_profits()), sort="profit")

    assert ranked[0].label == "Mapped"
    assert ranked[-1].label == "Mystery"
    assert not ranked[-1].is_complete


def test_products_can_be_ranked_by_orders_and_free_rows_filtered(make_product, make_order):
    hoodie = make_product(title="Hoodie", variants=[("Black", "L", "34.99")])
    magnet = make_product(title="Verse download", variants=[("Digital", "", "0.00")])
    make_order(lines=[(hoodie.variants.get(), 1, "34.99")])
    make_order(lines=[(magnet.variants.get(), 1, "0.00")], shipping_charged="0.00")
    make_order(lines=[(magnet.variants.get(), 1, "0.00")], shipping_charged="0.00")

    ranked = rank_products(all_product_performance(compute_order_profits()), sort="orders")

    assert ranked[0].label == "Verse download"
    assert ranked[0].is_free
    paid = filter_product_rows(ranked, hide_free=True)
    assert [row.label for row in paid] == ["Hoodie"]


def test_trading_by_month_splits_orders_onto_the_month_they_were_placed(
    make_product, map_variant, make_order
):
    product = make_product(variants=[("Black", "L", "20.00")])
    variant = product.variants.get()
    map_variant(variant, cost="8.00")
    make_order(lines=[(variant, 1, "20.00")], when=date(2026, 1, 10), shipping_charged="0.00")
    make_order(lines=[(variant, 2, "20.00")], when=date(2026, 2, 10), shipping_charged="0.00")

    months = trading_by_month(
        compute_order_profits(),
        DateRange(date(2026, 1, 1), date(2026, 2, 28), "Jan–Feb"),
    )

    assert [row.label for row in months] == ["Jan 2026", "Feb 2026"]
    assert months[0].units == 1
    assert months[1].units == 2


def test_refund_rate_is_reported_per_product(
    make_product, map_variant, make_order, refund_order
):
    product = make_product(variants=[("Black", "L", "20.00")])
    variant = product.variants.get()
    map_variant(variant, cost="8.00")
    make_order(lines=[(variant, 3, "20.00")])
    returned = make_order(lines=[(variant, 1, "20.00")])
    refund_order(returned, amount="20.00")

    performance = product_performance(compute_order_profits(), product)

    assert performance.units == 4
    assert performance.refunded_units == Decimal("1")
    assert performance.refund_rate_pct == Decimal("25.00")


def test_a_fully_refunded_mapped_product_is_a_loss_not_a_blank(
    make_product, map_variant, make_order, refund_order
):
    """Refunded POD still cost the shirt. Profit and margin must stay visible."""
    product = make_product(title="Returned tee", variants=[("Black", "L", "24.99")])
    variant = product.variants.get()
    map_variant(variant, cost="8.10")
    order = make_order(lines=[(variant, 1, "24.99")], shipping_charged="0.00")
    refund_order(order, amount="24.99")

    performance = product_performance(compute_order_profits(), product)

    assert performance.net_revenue == Decimal("0.00")
    assert performance.refunds == Decimal("24.99")
    assert not performance.is_free
    assert performance.is_complete
    assert performance.profit < Decimal("0.00")
    assert performance.margin_pct is not None
    assert performance.margin_pct < Decimal("0.00")
    kept = filter_product_rows([performance], hide_free=True)
    assert kept == [performance]


def test_revenue_share_is_of_the_filtered_set(make_product, make_order):
    hoodie = make_product(title="Hoodie", variants=[("Black", "L", "40.00")])
    tee = make_product(title="Tee", variants=[("Black", "L", "10.00")])
    make_order(lines=[(hoodie.variants.get(), 2, "40.00")], shipping_charged="0.00")
    make_order(lines=[(tee.variants.get(), 2, "10.00")], shipping_charged="0.00")

    rows = all_product_performance(compute_order_profits())
    attach_revenue_share(rows)
    by_name = {row.label: row.revenue_share_pct for row in rows}

    assert by_name["Hoodie"] == Decimal("80.00")
    assert by_name["Tee"] == Decimal("20.00")

    tee_only = [row for row in rows if row.label == "Tee"]
    attach_revenue_share(tee_only)
    assert tee_only[0].revenue_share_pct == Decimal("100.00")


# ---------------------------------------------------------------------------
# Data health
# ---------------------------------------------------------------------------


def test_the_unmapped_report_weights_by_units_actually_sold(
    make_product, map_variant, make_order
):
    """An unmapped variant nobody buys does not damage the figures; a bestseller does."""
    product = make_product(variants=[("Black", "L", "24.99"), ("White", "S", "24.99")])
    mapped, unmapped = product.variants.order_by("position")
    map_variant(mapped, cost="8.10")
    make_order(lines=[(mapped, 9, "24.99"), (unmapped, 1, "24.99")])

    report = unmapped_variant_report()

    assert report["mapped_units"] == 9
    assert report["unmapped_units"] == 1
    assert report["mapped_pct"] == Decimal("90.00")
    assert [v.pk for v in report["unmapped_variants"]] == [unmapped.pk]


def test_variant_options_are_normalised_so_analytics_can_group_them(make_product):
    product = make_product(variants=[("black", "Large", "24.99")])
    variant = product.variants.get()

    assert variant.colour == "Black"
    assert variant.size == "L"
    assert variant.display_options == "Black / L"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Large", "L"),
        ("large", "L"),
        ("LG", "L"),
        ("XXL", "2XL"),
        ("2x", "2XL"),
        ("2x-large", "2XL"),
        ("Extra Small", "XS"),
        ("One Size", "One Size"),
        ("", ""),
    ],
)
def test_size_normalisation(raw, expected):
    assert normalise_size(raw) == expected
