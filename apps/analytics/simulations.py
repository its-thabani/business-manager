"""What-if simulations over real historical orders.

Nothing here writes to the books. The baseline is the same profit engine the
order and product pages use. The projection applies stated assumptions on top
and lists those assumptions so a number is never mistaken for a fact.

If a sold line has no supplier cost, projected profit stays unknown unless the
scenario itself supplies a unit cost. A missing cost is never treated as £0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from apps.analytics.profitability import LineProfit, OrderProfit, Performance, _vat_on_net
from apps.core.money import ZERO, quantise, safe_divide
from apps.core.periods import DateRange


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return quantise(Decimal(str(value)))


@dataclass
class SimulationSpec:
    """Assumptions the user typed. Every field is optional."""

    new_price: Decimal | None = None
    price_change: Decimal | None = None
    volume_change_pct: Decimal | None = None
    new_unit_cost: Decimal | None = None
    cost_change: Decimal | None = None
    cost_change_pct: Decimal | None = None
    free_shipping: bool = False
    discount_pct: Decimal | None = None
    product_id: int | None = None

    def assumptions(self) -> list[str]:
        rows = []
        if self.new_price is not None:
            rows.append(f"Selling price set to £{self.new_price}")
        if self.price_change is not None:
            sign = "+" if self.price_change >= 0 else "−"
            rows.append(f"Selling price {sign}£{abs(self.price_change)} per unit")
        if self.volume_change_pct is not None:
            sign = "+" if self.volume_change_pct >= 0 else ""
            rows.append(f"Volume {sign}{self.volume_change_pct}% (an assumption, not a forecast)")
        if self.new_unit_cost is not None:
            rows.append(f"Supplier unit cost set to £{self.new_unit_cost}")
        if self.cost_change is not None:
            sign = "+" if self.cost_change >= 0 else "−"
            rows.append(f"Supplier unit cost {sign}£{abs(self.cost_change)}")
        if self.cost_change_pct is not None:
            sign = "+" if self.cost_change_pct >= 0 else ""
            rows.append(f"Supplier unit cost {sign}{self.cost_change_pct}%")
        if self.free_shipping:
            rows.append("Customer is not charged for postage; supplier postage is still paid")
        if self.discount_pct is not None:
            rows.append(f"Extra {self.discount_pct}% off product revenue")
        return rows


@dataclass
class SimulationResult:
    spec: SimulationSpec
    date_range: DateRange | None
    label: str
    baseline: Performance
    projected: Performance
    notes: list[str] = field(default_factory=list)
    break_even_volume_pct: Decimal | None = None

    @property
    def revenue_delta(self) -> Decimal:
        return quantise(self.projected.net_revenue - self.baseline.net_revenue)

    @property
    def profit_delta(self) -> Decimal | None:
        if not self.baseline.is_complete or not self.projected.is_complete:
            return None
        return quantise(self.projected.profit - self.baseline.profit)

    @property
    def margin_delta(self) -> Decimal | None:
        if self.baseline.margin_pct is None or self.projected.margin_pct is None:
            return None
        return quantise(self.projected.margin_pct - self.baseline.margin_pct)

    @property
    def assumptions(self) -> list[str]:
        return self.spec.assumptions()


def spec_from_query(params) -> SimulationSpec:
    """Read a GET form. Blank fields stay unused."""
    return SimulationSpec(
        new_price=_dec(params.get("new_price")),
        price_change=_dec(params.get("price_change")),
        volume_change_pct=_dec(params.get("volume_change_pct")),
        new_unit_cost=_dec(params.get("new_unit_cost")),
        cost_change=_dec(params.get("cost_change")),
        cost_change_pct=_dec(params.get("cost_change_pct")),
        free_shipping=str(params.get("free_shipping") or "") in {"1", "on", "true", "yes"},
        discount_pct=_dec(params.get("discount_pct")),
        product_id=int(params["product"]) if params.get("product") else None,
    )


def _volume_factor(spec: SimulationSpec) -> Decimal:
    if spec.volume_change_pct is None:
        return Decimal("1")
    return max(Decimal("0"), Decimal("1") + spec.volume_change_pct / Decimal("100"))


def _adjusted_unit_price(line: LineProfit, spec: SimulationSpec) -> Decimal:
    quantity = Decimal(line.quantity or 0)
    current = safe_divide(line.gross_revenue, quantity) or ZERO
    if spec.new_price is not None:
        return spec.new_price
    if spec.price_change is not None:
        return max(ZERO, quantise(current + spec.price_change))
    return current


def _adjusted_unit_cost(line: LineProfit, spec: SimulationSpec) -> Decimal | None:
    if spec.new_unit_cost is not None:
        return spec.new_unit_cost
    current = line.unit_cost
    if current is None:
        return None
    if spec.cost_change is not None:
        return max(ZERO, quantise(current + spec.cost_change))
    if spec.cost_change_pct is not None:
        return max(ZERO, quantise(current * (Decimal("1") + spec.cost_change_pct / Decimal("100"))))
    return current


def _scale(amount: Decimal, factor: Decimal) -> Decimal:
    return quantise(amount * factor)


def _roll_up(
    profits: list[OrderProfit],
    *,
    spec: SimulationSpec,
    date_range: DateRange | None,
    label: str,
    line_filter,
    track_assumed_cost: bool = False,
) -> Performance | tuple[Performance, bool]:
    """Build a Performance row, including postage charged to the customer.

    Product pages report line revenue only. Simulations include postage so
    "what if I offer free shipping" is a real change, not a no-op.
    """
    volume = _volume_factor(spec)
    extra_discount = (spec.discount_pct or ZERO) / Decimal("100")
    out = Performance(label=label, date_range=date_range, product_id=spec.product_id)
    order_ids: set[int] = set()
    used_assumed_cost = False

    for order_profit in profits:
        for line in order_profit.lines:
            if line_filter is not None and not line_filter(line):
                continue
            units = Decimal(line.quantity) * volume
            unit_price = _adjusted_unit_price(line, spec)
            gross = quantise(unit_price * units)
            discount_keep = _scale(line.discount, volume)
            if extra_discount:
                discount_keep = quantise(discount_keep + gross * extra_discount)
            refunds = _scale(line.refunded_amount, volume)
            postage = _scale(
                order_profit.shipping_charged * line.revenue_share / Decimal("100"),
                volume,
            )
            if spec.free_shipping:
                postage = ZERO
            net = quantise(gross - discount_keep - refunds + postage)

            unit_cost = _adjusted_unit_cost(line, spec)
            if unit_cost is None:
                product_cost = None
                out.lines_missing_cost += 1
            else:
                product_cost = quantise(unit_cost * units)
                if line.unit_cost is None:
                    used_assumed_cost = True

            # Supplier postage is still paid under free shipping.
            shipping_cost = _scale(line.allocated_shipping_cost, volume)
            if line.net_revenue:
                fee = quantise(line.allocated_payment_fee * (net / line.net_revenue) if not spec.free_shipping else line.allocated_payment_fee * volume)
            else:
                fee = _scale(line.allocated_payment_fee, volume)

            old_net = (line.product_cost or ZERO) + line.allocated_shipping_cost
            new_net = (product_cost or ZERO) + shipping_cost
            if product_cost is None:
                tax = ZERO
            elif old_net > 0 and line.allocated_tax:
                tax = quantise(new_net * (line.allocated_tax / old_net))
            else:
                tax = _vat_on_net(new_net)

            out.units += int(units.to_integral_value())
            out.lines_total += 1
            out.gross_revenue = quantise(out.gross_revenue + gross)
            out.discounts = quantise(out.discounts + discount_keep)
            out.refunds = quantise(out.refunds + refunds)
            out.net_revenue = quantise(out.net_revenue + net)
            out.refunded_units += line.refunded_quantity * volume
            out.shipping_cost = quantise(out.shipping_cost + shipping_cost)
            out.payment_fees = quantise(out.payment_fees + fee)
            out.supplier_tax = quantise(out.supplier_tax + tax)
            if product_cost is not None:
                out.supplier_cost = quantise(out.supplier_cost + product_cost)
            order_ids.add(line.line.order_id)

    out.orders = len(order_ids)
    if track_assumed_cost:
        return out, used_assumed_cost
    return out


def simulate(
    profits: list[OrderProfit],
    spec: SimulationSpec,
    *,
    date_range: DateRange | None = None,
    label: str = "Shop",
) -> SimulationResult:
    """Replay historical lines under ``spec``. Real rows are not changed."""
    product_id = spec.product_id
    line_filter = (lambda lp: lp.line.product_id == product_id) if product_id else None
    baseline = _roll_up(
        profits,
        spec=SimulationSpec(product_id=product_id),
        date_range=date_range,
        label=label,
        line_filter=line_filter,
    )
    projected, used_assumed_cost = _roll_up(
        profits,
        spec=spec,
        date_range=date_range,
        label=f"{label} (projected)",
        line_filter=line_filter,
        track_assumed_cost=True,
    )
    notes: list[str] = []

    if used_assumed_cost:
        notes.append(
            "Some lines had no recorded supplier cost. The scenario cost was used "
            "for those lines and is an assumption, not a historical fact."
        )
    if not spec.assumptions():
        notes.append("No assumptions entered, so the projection matches the baseline.")

    result = SimulationResult(
        spec=spec,
        date_range=date_range,
        label=label,
        baseline=baseline,
        projected=projected,
        notes=notes,
        break_even_volume_pct=None,
    )

    if spec.free_shipping:
        postage = ZERO
        counted = 0
        for order_profit in profits:
            lines = [
                lp
                for lp in order_profit.lines
                if product_id is None or lp.line.product_id == product_id
            ]
            if not lines:
                continue
            postage += order_profit.shipping_charged
            counted += 1
        if counted and baseline.avg_profit_per_order and baseline.avg_profit_per_order > 0:
            extra = quantise(postage) / baseline.avg_profit_per_order
            result.break_even_volume_pct = quantise(extra / Decimal(counted) * 100)
            result.notes.append(
                f"Free shipping forgoes £{quantise(postage)} of postage charged. "
                f"About {result.break_even_volume_pct}% more orders at the current "
                f"profit per order would be needed to break even."
            )
        elif spec.free_shipping:
            result.notes.append(
                "Free-shipping break-even is unknown because profit per order is unknown."
            )

    return result
