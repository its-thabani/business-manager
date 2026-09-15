"""Order, product and variant profitability.

This is where "did I actually make money on that" gets answered, and it is built
around one rule: **never present a guess as a fact**. Every cost carries a
``Basis`` saying where it came from, and when a cost cannot be known the result
says so instead of quietly substituting zero. A profit figure with a silently
missing supplier cost is worse than no figure at all, because it looks credible.

Costs are resolved in descending order of trustworthiness:

1. A linked supplier order — what Inkthreadable actually charged.
2. A per-line cost override — what you know it cost.
3. The supplier price list, as it stood on the day the order was placed.
4. Shopify Cost per item on the variant, if one was entered. Never invented.
5. Nothing, in which case the line is flagged and excluded from cost totals.

Two treatments in here are specific to print-on-demand and worth stating plainly.

**Refunded items still cost money.** With POD there is no stock to restock: the
garment was printed, shipped and paid for. So supplier cost is charged on the
full quantity ordered, not the quantity kept, which makes a refund cost the
business the sale *and* the item. Reported margins are lower than a naive
calculation and that is the point.

**Order-level costs are allocated pro-rata by line revenue.** Postage and card
fees are charged per order, not per item, so attributing them to a variant
requires a choice. Revenue-weighting means a £40 hoodie carries more of the
postage than a £5 sticker in the same parcel, which is what makes "orders with
several items are more profitable per item" a measurable effect rather than an
assertion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Prefetch, Sum

from apps.catalog.models import ProductVariant, size_sort_key
from apps.core.money import ZERO, margin_pct, pct_change, quantise, safe_divide
from apps.core.periods import DateRange
from apps.sales.models import Order, OrderLine


class Basis:
    """Where a number came from. Reported alongside every cost."""

    ACTUAL = "actual"
    """Taken from what the supplier or payment provider actually charged."""

    PRICE_LIST = "price_list"
    """From the supplier price list as it stood on the order date."""

    SHOPIFY = "shopify"
    """The Cost per item entered on the Shopify variant. Only used when no supplier cost exists."""

    ESTIMATED = "estimated"
    """Derived from a stated assumption, not from any record of the real amount."""

    MISSING = "missing"
    """Not knowable from the data available. Excluded from totals."""

    NOT_APPLICABLE = "n/a"
    """Genuinely zero, e.g. no postage was charged."""

    LABELS = {
        ACTUAL: "actual",
        PRICE_LIST: "from price list",
        SHOPIFY: "from Shopify",
        ESTIMATED: "estimated",
        MISSING: "unknown",
        NOT_APPLICABLE: "not applicable",
    }

    #: Bases that mean the figure is trustworthy enough to call a fact.
    RELIABLE = (ACTUAL, NOT_APPLICABLE)


def _assumptions() -> dict:
    """Fee and cost assumptions, overridable in settings.

    Kept in one place and echoed into every result that relies on them, so a
    projected figure can always be traced back to the assumption behind it.
    """
    defaults = {
        "payment_fee_percent": Decimal("1.5"),
        "payment_fee_fixed": Decimal("0.20"),
        "tax_treatment": "exclude",
    }
    defaults.update(getattr(settings, "PROFIT_ASSUMPTIONS", {}) or {})
    return defaults


class CostResolver:
    """Resolves supplier unit costs for many variants without N+1 queries.

    Effective-dated cost lookup is inherently per-variant-per-date, which would
    issue a query per order line. This loads every mapping and cost row once and
    resolves in memory.
    """

    def __init__(self, variant_ids: list[int] | None = None):
        self._costs: dict[int, list[tuple[date, Decimal]]] = {}
        self._overrides: dict[int, Decimal] = {}
        self._shopify: dict[int, Decimal] = {}
        self._mapped: set[int] = set()
        self._not_applicable: set[int] = set()
        self._load(variant_ids)

    def _load(self, variant_ids: list[int] | None) -> None:
        variants = ProductVariant.objects.select_related(
            "product",
            "product__supplier_product_mapping",
            "supplier_mapping",
            "supplier_mapping__supplier_variant",
        ).prefetch_related("supplier_mapping__supplier_variant__costs")
        if variant_ids is not None:
            variants = variants.filter(pk__in=variant_ids)

        for variant in variants:
            mapping = getattr(variant, "supplier_mapping", None)
            if mapping is not None:
                self._mapped.add(variant.pk)
                if mapping.cost_override is not None:
                    self._overrides[variant.pk] = mapping.cost_override
                    continue
                rows = sorted(
                    ((c.effective_from, c.unit_cost) for c in mapping.supplier_variant.costs.all()),
                    key=lambda row: row[0],
                )
                if rows:
                    self._costs[variant.pk] = rows
                continue
            product_mapping = getattr(variant.product, "supplier_product_mapping", None)
            if product_mapping and product_mapping.no_supplier:
                self._mapped.add(variant.pk)
                self._not_applicable.add(variant.pk)
            elif variant.shopify_unit_cost is not None:
                self._shopify[variant.pk] = variant.shopify_unit_cost

    def is_mapped(self, variant_id: int | None) -> bool:
        return variant_id is not None and variant_id in self._mapped

    def unit_cost(self, variant_id: int | None, when: date) -> tuple[Decimal | None, str]:
        """Unit cost for a variant on a date, with the basis for it."""
        if variant_id is None:
            return None, Basis.MISSING
        if variant_id in self._not_applicable:
            return ZERO, Basis.NOT_APPLICABLE
        if variant_id in self._overrides:
            return self._overrides[variant_id], Basis.ACTUAL
        rows = self._costs.get(variant_id)
        if not rows:
            if variant_id in self._shopify:
                return self._shopify[variant_id], Basis.SHOPIFY
            return None, Basis.MISSING
        applicable = [cost for effective_from, cost in rows if effective_from <= when]
        if not applicable:
            # Costs exist but all start after this order. Using a later cost would
            # misstate history, so report it as unknown instead.
            return None, Basis.MISSING
        return applicable[-1], Basis.PRICE_LIST


@dataclass
class LineProfit:
    """Profitability of a single order line."""

    line: OrderLine
    quantity: int
    gross_revenue: Decimal
    discount: Decimal
    refunded_amount: Decimal
    refunded_quantity: Decimal
    net_revenue: Decimal

    unit_cost: Decimal | None
    cost_basis: str
    product_cost: Decimal | None

    allocated_shipping_cost: Decimal
    allocated_payment_fee: Decimal
    revenue_share: Decimal

    @property
    def total_cost(self) -> Decimal | None:
        if self.product_cost is None:
            return None
        return quantise(self.product_cost + self.allocated_shipping_cost + self.allocated_payment_fee)

    @property
    def contribution_profit(self) -> Decimal | None:
        total = self.total_cost
        return None if total is None else quantise(self.net_revenue - total)

    @property
    def sales_for_margin(self) -> Decimal:
        if self.net_revenue == ZERO and self.refunded_amount:
            return self.refunded_amount
        return self.net_revenue

    @property
    def margin_pct(self) -> Decimal | None:
        profit = self.contribution_profit
        return None if profit is None else margin_pct(profit, self.sales_for_margin)

    @property
    def cost_is_known(self) -> bool:
        return self.product_cost is not None

    @property
    def variant_label(self) -> str:
        if self.line.variant:
            return self.line.variant.display_options
        return self.line.variant_title or "—"


@dataclass
class OrderProfit:
    """The full revenue-and-cost breakdown for one order.

    Mirrors how the figure would be written out by hand, so that opening an order
    shows exactly how its profit was arrived at rather than a single number to be
    taken on trust.
    """

    order: Order

    # --- Revenue ---
    product_revenue: Decimal = ZERO
    shipping_charged: Decimal = ZERO
    discounts: Decimal = ZERO
    refunds: Decimal = ZERO
    tax: Decimal = ZERO
    tax_treatment: str = "exclude"

    # --- Costs ---
    supplier_product_cost: Decimal | None = None
    supplier_product_cost_basis: str = Basis.MISSING
    supplier_shipping_cost: Decimal | None = None
    supplier_shipping_cost_basis: str = Basis.MISSING
    payment_fee: Decimal | None = None
    payment_fee_basis: str = Basis.MISSING

    lines: list[LineProfit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: dict = field(default_factory=dict)

    # -- Revenue ----------------------------------------------------------

    @property
    def net_sales(self) -> Decimal:
        """What the sale was worth, after discounts and refunds.

        Tax is excluded by default: VAT collected on behalf of HMRC is not income.
        Set ``PROFIT_ASSUMPTIONS['tax_treatment'] = 'include'`` if the business is
        not VAT registered and you would rather see gross figures.
        """
        total = self.product_revenue + self.shipping_charged - self.discounts - self.refunds
        if self.tax_treatment == "exclude":
            total -= self.tax
        return quantise(total)

    @property
    def expected_cash(self) -> Decimal:
        """What should land in the bank after card fees, for payout matching."""
        return quantise(self.net_sales - (self.payment_fee or ZERO))

    # -- Costs ------------------------------------------------------------

    @property
    def known_costs(self) -> list[Decimal]:
        return [c for c in (self.supplier_product_cost, self.supplier_shipping_cost, self.payment_fee) if c is not None]

    @property
    def total_costs(self) -> Decimal:
        """Sum of costs that are actually known. See ``is_complete``."""
        return quantise(sum(self.known_costs, ZERO))

    @property
    def contribution_profit(self) -> Decimal | None:
        """Profit, or None when a cost is missing and profit cannot be stated."""
        return None if not self.is_complete else quantise(self.net_sales - self.total_costs)

    @property
    def provisional_contribution_profit(self) -> Decimal:
        """Profit using only known costs, which overstates when costs are missing.

        Useful for ranking and aggregation; never present it as the answer without
        also showing ``missing_cost_labels``.
        """
        return quantise(self.net_sales - self.total_costs)

    @property
    def margin_pct(self) -> Decimal | None:
        profit = self.contribution_profit
        return None if profit is None else margin_pct(profit, self.net_sales)

    @property
    def missing_cost_labels(self) -> list[str]:
        missing = []
        if self.supplier_product_cost_basis == Basis.MISSING:
            missing.append("supplier product cost")
        if self.supplier_shipping_cost_basis == Basis.MISSING:
            missing.append("supplier shipping cost")
        if self.payment_fee_basis == Basis.MISSING:
            missing.append("payment fee")
        return missing

    @property
    def is_complete(self) -> bool:
        """Whether every cost component is known or estimated."""
        return not self.missing_cost_labels

    @property
    def is_fully_actual(self) -> bool:
        """Whether no part of the figure relies on an estimate or a price list."""
        return all(
            basis in Basis.RELIABLE
            for basis in (
                self.supplier_product_cost_basis,
                self.supplier_shipping_cost_basis,
                self.payment_fee_basis,
            )
        )

    @property
    def units(self) -> int:
        return sum(line.quantity for line in self.lines)

    def as_dict(self) -> dict:
        return {
            "order": self.order.name,
            "placed_on": self.order.placed_on,
            "product_revenue": self.product_revenue,
            "shipping_charged": self.shipping_charged,
            "discounts": self.discounts,
            "refunds": self.refunds,
            "tax": self.tax,
            "net_sales": self.net_sales,
            "supplier_product_cost": self.supplier_product_cost,
            "supplier_shipping_cost": self.supplier_shipping_cost,
            "payment_fee": self.payment_fee,
            "total_costs": self.total_costs,
            "contribution_profit": self.contribution_profit,
            "margin_pct": self.margin_pct,
            "is_complete": self.is_complete,
            "missing": self.missing_cost_labels,
            "warnings": self.warnings,
        }


def _product_cost_basis(bases: list[str]) -> str:
    """Roll per-line cost bases up to one order-level label."""
    unique = set(bases)
    if unique == {Basis.NOT_APPLICABLE}:
        return Basis.NOT_APPLICABLE
    if unique <= {Basis.ACTUAL, Basis.NOT_APPLICABLE}:
        return Basis.ACTUAL
    if unique <= {Basis.SHOPIFY, Basis.NOT_APPLICABLE}:
        return Basis.SHOPIFY
    if Basis.ESTIMATED in unique:
        return Basis.ESTIMATED
    return Basis.PRICE_LIST


def _orders_with_everything(queryset):
    """Load orders with all the rows profitability needs, in a fixed query count."""
    return queryset.select_related("customer").prefetch_related(
        Prefetch(
            "lines",
            queryset=OrderLine.objects.select_related("variant", "variant__product", "product"),
        ),
        "refunds",
        "refunds__lines",
        "supplier_orders",
    )


def compute_order_profit(
    order: Order,
    *,
    resolver: CostResolver | None = None,
) -> OrderProfit:
    """Break an order down into revenue, costs and contribution profit."""
    assumptions = _assumptions()
    lines = list(order.lines.all())
    if resolver is None:
        resolver = CostResolver([line.variant_id for line in lines if line.variant_id])

    result = OrderProfit(
        order=order,
        product_revenue=quantise(sum((line.gross_revenue for line in lines), ZERO)),
        shipping_charged=quantise(order.shipping_charged or ZERO),
        discounts=quantise(order.total_discounts or ZERO),
        refunds=quantise(order.refunded_amount),
        tax=quantise(order.total_tax or ZERO),
        tax_treatment=assumptions["tax_treatment"],
        assumptions=assumptions,
    )

    _check_discount_consistency(result, lines)

    # --- Supplier product cost, per line ---------------------------------
    # Charged on the full quantity: a printed garment is a sunk cost even when
    # the customer is refunded.
    line_costs: dict[int, Decimal | None] = {}
    line_bases: dict[int, str] = {}
    for line in lines:
        if line.unit_cost_override is not None:
            unit_cost, basis = line.unit_cost_override, Basis.ACTUAL
        else:
            unit_cost, basis = resolver.unit_cost(line.variant_id, order.placed_on)
        line_costs[line.pk] = None if unit_cost is None else quantise(unit_cost * line.quantity)
        line_bases[line.pk] = basis

    estimated_total = sum((c for c in line_costs.values() if c is not None), ZERO)
    all_costs_known = all(c is not None for c in line_costs.values()) and bool(lines)

    linked = list(order.supplier_orders.all())
    product_charges = [so.product_cost for so in linked if so.product_cost is not None]
    shipping_charges = [so.shipping_cost for so in linked if so.shipping_cost is not None]
    if product_charges:
        # What Inkthreadable actually charged beats any price list. Split
        # fulfilments (two Inkthreadable jobs for one Shopify order) are summed.
        # Line costs are rescaled to that total so per-variant figures stay
        # consistent with the order instead of the two quietly disagreeing.
        actual = quantise(sum(product_charges, ZERO))
        result.supplier_product_cost = actual
        result.supplier_product_cost_basis = Basis.ACTUAL
        line_costs = _rescale_line_costs(line_costs, lines, estimated_total, actual)
        line_bases = {line.pk: Basis.ACTUAL for line in lines}
        if all_costs_known and estimated_total and abs(actual - estimated_total) > Decimal("0.50"):
            result.warnings.append(
                f"Supplier charged £{actual} but the price list implies £{quantise(estimated_total)}. "
                f"Using the actual charge; the price list may be out of date."
            )
    elif all_costs_known:
        result.supplier_product_cost = quantise(estimated_total)
        result.supplier_product_cost_basis = _product_cost_basis(
            [line_bases[line.pk] for line in lines]
        )
    else:
        unmapped = [line for line in lines if line_costs[line.pk] is None]
        result.warnings.append(
            f"{len(unmapped)} of {len(lines)} line(s) have no supplier cost, so profit "
            f"cannot be calculated: {', '.join(sorted({line.title for line in unmapped}))}. "
            f"Map the variant to an Inkthreadable product to fix this."
        )

    # --- Supplier shipping -----------------------------------------------
    if shipping_charges:
        result.supplier_shipping_cost = quantise(sum(shipping_charges, ZERO))
        result.supplier_shipping_cost_basis = Basis.ACTUAL
    elif not any(line.requires_shipping for line in lines):
        result.supplier_shipping_cost = ZERO
        result.supplier_shipping_cost_basis = Basis.NOT_APPLICABLE
    else:
        result.warnings.append(
            "No supplier shipping cost recorded for this order. Link the "
            "Inkthreadable order to include postage in the profit figure."
        )

    # --- Payment fee ------------------------------------------------------
    if order.payment_fee is not None:
        result.payment_fee = quantise(order.payment_fee)
        result.payment_fee_basis = Basis.ACTUAL
    elif order.total_price:
        percent = Decimal(assumptions["payment_fee_percent"])
        fixed = Decimal(assumptions["payment_fee_fixed"])
        result.payment_fee = quantise(order.total_price * percent / 100 + fixed)
        result.payment_fee_basis = Basis.ESTIMATED
    else:
        result.payment_fee = ZERO
        result.payment_fee_basis = Basis.NOT_APPLICABLE

    result.lines = _build_line_profits(order, lines, line_costs, line_bases, result)
    return result


def _rescale_line_costs(
    line_costs: dict[int, Decimal | None],
    lines: list[OrderLine],
    estimated_total: Decimal,
    actual: Decimal,
) -> dict[int, Decimal | None]:
    """Spread an actual supplier charge across lines.

    Proportionally to the price-list estimate where one exists, otherwise evenly
    by quantity, so every line ends up with a cost that sums to what was paid.
    """
    if estimated_total > 0:
        scale = actual / estimated_total
        return {
            pk: (None if cost is None else quantise(cost * scale)) for pk, cost in line_costs.items()
        }
    total_units = sum(line.quantity for line in lines) or 1
    per_unit = actual / total_units
    return {line.pk: quantise(per_unit * line.quantity) for line in lines}


def _check_discount_consistency(result: OrderProfit, lines: list[OrderLine]) -> None:
    """Warn when line discounts and the order discount total disagree.

    Shopify allocates discounts to lines, but an order-level shipping discount is
    not allocated anywhere. A large gap usually means the line allocation did not
    come through the sync intact, which would distort per-variant profit.
    """
    line_total = sum((line.discount_amount or ZERO for line in lines), ZERO)
    if result.discounts and abs(result.discounts - line_total) > Decimal("0.05"):
        result.warnings.append(
            f"Order discount of £{result.discounts} does not match the £{quantise(line_total)} "
            f"allocated across lines, so per-variant profit may be slightly off."
        )


def _build_line_profits(
    order: Order,
    lines: list[OrderLine],
    line_costs: dict[int, Decimal | None],
    line_bases: dict[int, str],
    result: OrderProfit,
) -> list[LineProfit]:
    """Attach revenue, cost and a share of order-level costs to each line."""
    net_revenues = {line.pk: line.net_revenue for line in lines}
    total_net = sum(net_revenues.values(), ZERO)
    total_units = sum(line.quantity for line in lines)

    shipping_to_share = result.supplier_shipping_cost or ZERO
    fee_to_share = result.payment_fee or ZERO

    out = []
    for line in lines:
        if total_net > 0:
            share = net_revenues[line.pk] / total_net
        elif total_units:
            share = Decimal(line.quantity) / Decimal(total_units)
        else:
            share = ZERO

        refund_total = line.refunded_amount
        out.append(
            LineProfit(
                line=line,
                quantity=line.quantity,
                gross_revenue=quantise(line.gross_revenue),
                discount=quantise(line.discount_amount or ZERO),
                refunded_amount=quantise(refund_total),
                refunded_quantity=line.refunded_quantity,
                net_revenue=quantise(net_revenues[line.pk] - refund_total),
                unit_cost=(
                    None
                    if line_costs[line.pk] is None
                    else quantise(line_costs[line.pk] / line.quantity)
                    if line.quantity
                    else ZERO
                ),
                cost_basis=line_bases[line.pk],
                product_cost=line_costs[line.pk],
                allocated_shipping_cost=quantise(shipping_to_share * share),
                allocated_payment_fee=quantise(fee_to_share * share),
                revenue_share=quantise(share * 100),
            )
        )
    return out


def compute_order_profits(
    date_range: DateRange | None = None,
    *,
    queryset=None,
) -> list[OrderProfit]:
    """Profit for many orders, loaded efficiently and costed with one resolver."""
    orders = queryset if queryset is not None else Order.objects.countable()
    if date_range is not None:
        orders = orders.in_range(date_range)
    orders = list(_orders_with_everything(orders))

    variant_ids = {
        line.variant_id for order in orders for line in order.lines.all() if line.variant_id
    }
    resolver = CostResolver(list(variant_ids))
    return [compute_order_profit(order, resolver=resolver) for order in orders]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class Performance:
    """Aggregated trading performance for a set of order lines.

    ``lines_missing_cost`` is deliberately prominent. An aggregate built from
    partially-costed lines overstates profit if missing costs are treated as
    zero, so the official ``profit`` is only shown when every sold line has a
    cost. ``estimated_profit`` applies the margin of the known-cost lines to
    the rest and must be labelled as an estimate.
    """

    label: str
    date_range: DateRange | None = None
    product_id: int | None = None
    group_id: int | None = None
    catalog_status: str = ""
    revenue_share_pct: Decimal | None = None

    orders: int = 0
    units: int = 0
    gross_revenue: Decimal = ZERO
    discounts: Decimal = ZERO
    refunds: Decimal = ZERO
    net_revenue: Decimal = ZERO

    supplier_cost: Decimal = ZERO
    shipping_cost: Decimal = ZERO
    payment_fees: Decimal = ZERO

    lines_total: int = 0
    lines_missing_cost: int = 0
    known_lines: int = 0
    known_units: int = 0
    known_net_revenue: Decimal = ZERO
    known_profit: Decimal = ZERO
    refunded_units: Decimal = ZERO

    @property
    def total_cost(self) -> Decimal:
        return quantise(self.supplier_cost + self.shipping_cost + self.payment_fees)

    @property
    def sales_for_margin(self) -> Decimal:
        """Revenue used for the margin %.

        A fully refunded product has £0 net sales, but the original ticket is
        still the right denominator — otherwise margin becomes a blank and the
        print cost looks like it vanished.
        """
        if self.net_revenue == ZERO and self.refunds:
            return self.refunds
        return self.net_revenue

    @property
    def profit(self) -> Decimal:
        return quantise(self.net_revenue - self.total_cost)

    @property
    def margin_pct(self) -> Decimal | None:
        return margin_pct(self.profit, self.sales_for_margin)

    @property
    def known_margin_pct(self) -> Decimal | None:
        """Margin on sold lines that actually have a cost. Used for estimates."""
        if not self.known_lines:
            return None
        return margin_pct(self.known_profit, self.known_net_revenue)

    @property
    def estimated_profit(self) -> Decimal | None:
        """Apply the known-line margin to all sales. Never the official figure."""
        if self.is_complete or not self.known_lines:
            return None
        rate = safe_divide(self.known_profit, self.known_net_revenue)
        if rate is None:
            return None
        return quantise(self.net_revenue * rate)

    @property
    def estimated_margin_pct(self) -> Decimal | None:
        if self.is_complete:
            return None
        return self.known_margin_pct

    @property
    def displayed_profit(self) -> Decimal | None:
        if self.is_complete:
            return self.profit
        return self.estimated_profit

    @property
    def displayed_margin_pct(self) -> Decimal | None:
        if self.is_complete:
            return self.margin_pct
        return self.estimated_margin_pct

    @property
    def is_estimate(self) -> bool:
        return not self.is_complete and self.estimated_profit is not None

    @property
    def profit_basis(self) -> str:
        if not self.lines_total:
            return ""
        if self.is_complete:
            return "complete"
        if self.is_estimate:
            return "estimate"
        return ""

    @property
    def avg_selling_price(self) -> Decimal | None:
        return safe_divide(self.net_revenue, Decimal(self.units)) if self.units else None

    @property
    def avg_profit_per_unit(self) -> Decimal | None:
        return safe_divide(self.profit, Decimal(self.units)) if self.units else None

    @property
    def avg_order_value(self) -> Decimal | None:
        return safe_divide(self.net_revenue, Decimal(self.orders)) if self.orders else None

    @property
    def avg_profit_per_order(self) -> Decimal | None:
        return safe_divide(self.profit, Decimal(self.orders)) if self.orders else None

    @property
    def refund_rate_pct(self) -> Decimal | None:
        return safe_divide(self.refunded_units * 100, Decimal(self.units)) if self.units else None

    @property
    def is_complete(self) -> bool:
        return self.lines_missing_cost == 0

    @property
    def is_free(self) -> bool:
        """Sold, but Shopify recorded £0 — usually a digital lead magnet.

        A fully refunded paid order also nets to £0. That is a loss (the print
        was already done), not a free download.
        """
        return (
            self.orders > 0
            and self.net_revenue == ZERO
            and self.refunds == ZERO
            and self.gross_revenue == ZERO
        )

    @property
    def completeness_pct(self) -> Decimal | None:
        if not self.lines_total:
            return None
        known = self.lines_total - self.lines_missing_cost
        return quantise(Decimal(known) / Decimal(self.lines_total) * 100)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "product_id": self.product_id,
            "orders": self.orders,
            "units": self.units,
            "gross_revenue": self.gross_revenue,
            "discounts": self.discounts,
            "refunds": self.refunds,
            "net_revenue": self.net_revenue,
            "supplier_cost": self.supplier_cost,
            "shipping_cost": self.shipping_cost,
            "payment_fees": self.payment_fees,
            "total_cost": self.total_cost,
            "profit": self.profit,
            "margin_pct": self.margin_pct,
            "avg_selling_price": self.avg_selling_price,
            "avg_profit_per_unit": self.avg_profit_per_unit,
            "avg_order_value": self.avg_order_value,
            "avg_profit_per_order": self.avg_profit_per_order,
            "refund_rate_pct": self.refund_rate_pct,
            "is_complete": self.is_complete,
            "completeness_pct": self.completeness_pct,
            "lines_missing_cost": self.lines_missing_cost,
            "known_units": self.known_units,
            "known_profit": self.known_profit,
            "known_margin_pct": self.known_margin_pct,
            "estimated_profit": self.estimated_profit,
            "is_estimate": self.is_estimate,
            "profit_basis": self.profit_basis,
        }


def _accumulate(performance: Performance, line_profit: LineProfit, order_ids: set) -> None:
    performance.lines_total += 1
    performance.units += line_profit.quantity
    performance.gross_revenue = quantise(performance.gross_revenue + line_profit.gross_revenue)
    performance.discounts = quantise(performance.discounts + line_profit.discount)
    performance.refunds = quantise(performance.refunds + line_profit.refunded_amount)
    performance.net_revenue = quantise(performance.net_revenue + line_profit.net_revenue)
    performance.refunded_units += line_profit.refunded_quantity
    performance.shipping_cost = quantise(
        performance.shipping_cost + line_profit.allocated_shipping_cost
    )
    performance.payment_fees = quantise(performance.payment_fees + line_profit.allocated_payment_fee)
    if line_profit.product_cost is None:
        performance.lines_missing_cost += 1
    else:
        performance.supplier_cost = quantise(performance.supplier_cost + line_profit.product_cost)
        performance.known_lines += 1
        performance.known_units += line_profit.quantity
        performance.known_net_revenue = quantise(
            performance.known_net_revenue + line_profit.net_revenue
        )
        if line_profit.contribution_profit is not None:
            performance.known_profit = quantise(
                performance.known_profit + line_profit.contribution_profit
            )
    order_ids.add(line_profit.line.order_id)


def summarise(
    profits: list[OrderProfit],
    *,
    label: str,
    date_range: DateRange | None = None,
    line_filter=None,
) -> Performance:
    """Aggregate line-level profitability across orders.

    ``line_filter`` selects which lines count, which is how the same routine
    serves whole-shop, per-product, per-group and per-variant views without
    duplicating any arithmetic.
    """
    performance = Performance(label=label, date_range=date_range)
    order_ids: set = set()
    for order_profit in profits:
        for line_profit in order_profit.lines:
            if line_filter is not None and not line_filter(line_profit):
                continue
            _accumulate(performance, line_profit, order_ids)
    performance.orders = len(order_ids)
    return performance


def product_performance(
    profits: list[OrderProfit],
    product,
    *,
    date_range: DateRange | None = None,
) -> Performance:
    result = summarise(
        profits,
        label=product.title,
        date_range=date_range,
        line_filter=lambda lp: lp.line.product_id == product.pk,
    )
    result.product_id = product.pk
    return result


def compare_performance(current: Performance, previous: Performance) -> dict:
    """Percentage change vs the previous window. Missing when a base is zero."""
    profit_pct = None
    if current.is_complete and previous.is_complete:
        profit_pct = pct_change(current.profit, previous.profit)
    return {
        "units_pct": pct_change(Decimal(current.units), Decimal(previous.units)),
        "revenue_pct": pct_change(current.net_revenue, previous.net_revenue),
        "profit_pct": profit_pct,
        "previous": previous,
    }


def all_product_performance(
    profits: list[OrderProfit],
    *,
    date_range: DateRange | None = None,
) -> list[Performance]:
    """One performance row per product that sold in the period."""
    buckets: dict[int | None, Performance] = {}
    order_ids: dict[int | None, set] = {}
    for order_profit in profits:
        for line_profit in order_profit.lines:
            key = line_profit.line.product_id
            if key not in buckets:
                buckets[key] = Performance(
                    label=line_profit.line.title,
                    date_range=date_range,
                    product_id=key,
                )
                order_ids[key] = set()
            _accumulate(buckets[key], line_profit, order_ids[key])
    for key, performance in buckets.items():
        performance.orders = len(order_ids[key])
    return rank_products(list(buckets.values()), sort="units")


def rank_products(
    rows: list[Performance],
    *,
    sort: str = "units",
    use_estimate: bool = False,
) -> list[Performance]:
    """Sort product or group rows.

    Incomplete rows rank last when sorting by money, unless ``use_estimate``
    (group totals) so an approximate profit can be compared.
    """
    if sort == "name":
        return sorted(rows, key=lambda row: row.label.casefold())
    if sort == "orders":
        return sorted(rows, key=lambda row: -row.orders)
    if sort == "refunds":
        return sorted(rows, key=lambda row: -row.refunded_units)
    if sort == "revenue":
        return sorted(rows, key=lambda row: -row.net_revenue)
    if sort == "share":
        return sorted(rows, key=lambda row: -(row.revenue_share_pct or ZERO))
    if sort == "profit":
        if use_estimate:
            return sorted(
                rows,
                key=lambda row: (row.displayed_profit is None, -(row.displayed_profit or ZERO)),
            )
        return sorted(
            rows,
            key=lambda row: (not row.is_complete, -(row.profit if row.is_complete else ZERO)),
        )
    if sort == "margin":
        if use_estimate:
            return sorted(
                rows,
                key=lambda row: (
                    row.displayed_margin_pct is None,
                    -(row.displayed_margin_pct or ZERO),
                ),
            )
        return sorted(
            rows,
            key=lambda row: (not row.is_complete, row.margin_pct is None, -(row.margin_pct or ZERO)),
        )
    return sorted(rows, key=lambda row: -row.units)


def filter_product_rows(
    rows: list[Performance],
    *,
    hide_free: bool = False,
    hide_zero_orders: bool = False,
    hide_inactive: bool = False,
) -> list[Performance]:
    """Apply table filters without changing the underlying profit figures."""
    out = rows
    if hide_zero_orders:
        out = [row for row in out if row.orders > 0]
    if hide_free:
        out = [row for row in out if not row.is_free]
    if hide_inactive:
        out = [
            row
            for row in out
            if row.catalog_status not in {"archived", "draft"}
        ]
    return out


def attach_revenue_share(rows: list[Performance]) -> Decimal:
    """Stamp each row with its % of the rows' combined net sales.

    The denominator is the filtered set, so Year to date, live-only, and
    hide-free all change the total the percentage is of.
    """
    total = sum((row.net_revenue for row in rows), ZERO)
    for row in rows:
        share = safe_divide(row.net_revenue, total)
        row.revenue_share_pct = quantise(share * 100) if share is not None else None
    return total


def trading_by_month(
    profits: list[OrderProfit],
    date_range: DateRange,
    *,
    line_filter=None,
) -> list[Performance]:
    """Shop or product trading, split into calendar months."""
    return [
        summarise(
            [row for row in profits if month.contains(row.order.placed_on)],
            label=month.label,
            date_range=month,
            line_filter=line_filter,
        )
        for month in date_range.months()
    ]


def group_performance(
    profits: list[OrderProfit],
    group,
    *,
    date_range: DateRange | None = None,
) -> Performance:
    product_ids = set(group.products.values_list("pk", flat=True))
    row = summarise(
        profits,
        label=group.name,
        date_range=date_range,
        line_filter=lambda lp: lp.line.product_id in product_ids,
    )
    row.group_id = group.pk
    row.product_id = None
    return row


def all_group_performance(
    profits: list[OrderProfit],
    groups,
    *,
    date_range: DateRange | None = None,
) -> list[Performance]:
    return [group_performance(profits, group, date_range=date_range) for group in groups]


def variant_performance(
    profits: list[OrderProfit],
    *,
    product=None,
    date_range: DateRange | None = None,
) -> list[Performance]:
    """Performance per variant, best margin first.

    Variants that sold nothing in the period are omitted rather than listed at
    zero, so the ranking reflects trading rather than catalogue size.
    """
    buckets: dict[int | None, Performance] = {}
    order_ids: dict[int | None, set] = {}

    for order_profit in profits:
        for line_profit in order_profit.lines:
            line = line_profit.line
            if product is not None and line.product_id != product.pk:
                continue
            key = line.variant_id
            if key not in buckets:
                label = (
                    line_profit.variant_label
                    if product is not None
                    else f"{line.title} — {line_profit.variant_label}"
                )
                buckets[key] = Performance(label=label, date_range=date_range)
                order_ids[key] = set()
            _accumulate(buckets[key], line_profit, order_ids[key])

    for key, performance in buckets.items():
        performance.orders = len(order_ids[key])

    return sorted(
        buckets.values(),
        key=lambda p: (not p.is_complete, p.margin_pct is None, -(p.margin_pct or ZERO)),
    )


def option_mix(profits: list[OrderProfit], product=None, *, attribute: str = "size") -> list[dict]:
    """Share of units sold by size or colour.

    Answers "most popular size" as a proportion of units, and pairs it with
    profit so a popular option that loses money is visible rather than flattering.
    """
    totals: dict[str, dict] = {}
    for order_profit in profits:
        for line_profit in order_profit.lines:
            line = line_profit.line
            if product is not None and line.product_id != product.pk:
                continue
            value = getattr(line.variant, attribute, "") if line.variant else ""
            if not value:
                value = "Unspecified"
            row = totals.setdefault(
                value,
                {"value": value, "units": 0, "net_revenue": ZERO, "profit": ZERO, "known": True},
            )
            row["units"] += line_profit.quantity
            row["net_revenue"] = quantise(row["net_revenue"] + line_profit.net_revenue)
            profit = line_profit.contribution_profit
            if profit is None:
                row["known"] = False
            else:
                row["profit"] = quantise(row["profit"] + profit)

    total_units = sum(row["units"] for row in totals.values())
    rows = list(totals.values())
    for row in rows:
        row["share_pct"] = (
            quantise(Decimal(row["units"]) / Decimal(total_units) * 100) if total_units else None
        )
        row["margin_pct"] = margin_pct(row["profit"], row["net_revenue"]) if row["known"] else None

    if attribute == "size":
        return sorted(rows, key=lambda r: size_sort_key(r["value"]))
    return sorted(rows, key=lambda r: r["units"], reverse=True)


def unmapped_variant_report() -> dict:
    """How much of what sold has no cost attached.

    Weighted by units sold, because an unmapped variant that never sells does not
    affect any figure, while an unmapped bestseller invalidates the lot.
    """
    sold = (
        OrderLine.objects.filter(order__is_test=False, order__cancelled_at__isnull=True)
        .values("variant_id")
        .annotate(units=Sum("quantity"))
    )
    resolver = CostResolver()
    mapped_units = unmapped_units = 0
    unmapped_variant_ids = []
    for row in sold:
        if resolver.is_mapped(row["variant_id"]):
            mapped_units += row["units"] or 0
        else:
            unmapped_units += row["units"] or 0
            if row["variant_id"]:
                unmapped_variant_ids.append(row["variant_id"])

    total = mapped_units + unmapped_units
    return {
        "mapped_units": mapped_units,
        "unmapped_units": unmapped_units,
        "total_units": total,
        "mapped_pct": quantise(Decimal(mapped_units) / Decimal(total) * 100) if total else None,
        "unmapped_variants": ProductVariant.objects.filter(
            pk__in=unmapped_variant_ids
        ).select_related("product"),
    }
