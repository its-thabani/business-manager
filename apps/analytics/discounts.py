"""Discount codes and how much revenue they gave away.

An order's discount is counted once. Multiple codes on the same order stay
together rather than inventing a split. Automatic markdowns with no code are
labelled, not dropped. Contribution profit given away is only stated when the
order's costs are complete — missing cost stays —.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.analytics.profitability import compute_order_profits
from apps.core.money import ZERO, quantise, safe_divide
from apps.core.periods import DateRange
from apps.sales.models import Order

NO_CODE = "Automatic / no code"


def _is_digital(order) -> bool:
    lines = list(order.lines.all())
    return bool(lines) and not any(line.requires_shipping for line in lines)


def normalize_codes(raw) -> list[str]:
    """Shopify usually stores a list of strings; objects are accepted too."""
    codes: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("code") or item.get("title") or "").strip()
        else:
            text = str(item).strip()
        if text:
            codes.append(text)
    return codes


def code_label(codes: list[str]) -> str:
    if not codes:
        return NO_CODE
    return " + ".join(sorted(codes, key=str.casefold))


@dataclass
class DiscountOrderRow:
    order_id: int
    name: str
    placed_on: date
    code: str
    amount: Decimal
    total_price: Decimal
    profit_impact: Decimal | None


@dataclass
class DiscountCodeRow:
    code: str
    orders: int = 0
    amount: Decimal = ZERO
    profit_impact: Decimal | None = None
    profit_known: int = 0
    profit_missing: int = 0

    @property
    def average(self) -> Decimal | None:
        value = safe_divide(self.amount, Decimal(self.orders)) if self.orders else None
        return quantise(value) if value is not None else None


@dataclass
class DiscountMetrics:
    date_range: DateRange
    hide_digital: bool
    universe: int = 0
    digital: int = 0
    discounted: int = 0
    amount: Decimal = ZERO
    list_revenue: Decimal = ZERO
    profit_impact: Decimal | None = None
    profit_known: int = 0
    profit_missing: int = 0
    rows: list[DiscountOrderRow] = field(default_factory=list)
    codes: list[DiscountCodeRow] = field(default_factory=list)

    @property
    def rate_pct(self) -> Decimal | None:
        value = (
            safe_divide(Decimal(self.discounted) * 100, Decimal(self.universe))
            if self.universe
            else None
        )
        return quantise(value) if value is not None else None

    @property
    def average(self) -> Decimal | None:
        value = safe_divide(self.amount, Decimal(self.discounted)) if self.discounted else None
        return quantise(value) if value is not None else None

    @property
    def of_list_pct(self) -> Decimal | None:
        value = safe_divide(self.amount * 100, self.list_revenue) if self.list_revenue else None
        return quantise(value) if value is not None else None


def compute_discounts(date_range: DateRange, *, hide_digital: bool = True) -> DiscountMetrics:
    """Discounts on countable orders in ``date_range``."""
    metrics = DiscountMetrics(date_range=date_range, hide_digital=hide_digital)
    orders = list(
        Order.objects.countable()
        .in_range(date_range)
        .prefetch_related("lines")
        .order_by("placed_on", "pk")
    )
    discounted_orders = []
    for order in orders:
        if _is_digital(order):
            metrics.digital += 1
            if hide_digital:
                continue
        metrics.universe += 1
        metrics.list_revenue = quantise(
            metrics.list_revenue
            + sum((line.unit_price * line.quantity for line in order.lines.all()), ZERO)
        )
        amount = quantise(order.total_discounts or ZERO)
        if amount <= ZERO:
            continue
        metrics.discounted += 1
        metrics.amount = quantise(metrics.amount + amount)
        discounted_orders.append(order)

    profits = {
        profit.order.pk: profit
        for profit in compute_order_profits(queryset=Order.objects.filter(pk__in=[o.pk for o in discounted_orders]))
    } if discounted_orders else {}

    buckets: dict[str, DiscountCodeRow] = {}
    known_impacts: list[Decimal] = []
    for order in discounted_orders:
        amount = quantise(order.total_discounts or ZERO)
        label = code_label(normalize_codes(order.discount_codes))
        profit = profits.get(order.pk)
        impact = amount if profit is not None and profit.is_complete else None
        row = DiscountOrderRow(
            order_id=order.pk,
            name=order.name or f"Order {order.pk}",
            placed_on=order.placed_on,
            code=label,
            amount=amount,
            total_price=quantise(order.total_price or ZERO),
            profit_impact=impact,
        )
        metrics.rows.append(row)

        bucket = buckets.get(label)
        if bucket is None:
            bucket = DiscountCodeRow(code=label)
            buckets[label] = bucket
        bucket.orders += 1
        bucket.amount = quantise(bucket.amount + amount)
        if impact is None:
            metrics.profit_missing += 1
            bucket.profit_missing += 1
        else:
            metrics.profit_known += 1
            bucket.profit_known += 1
            known_impacts.append(impact)
            bucket.profit_impact = quantise((bucket.profit_impact or ZERO) + impact)

    metrics.profit_impact = quantise(sum(known_impacts, ZERO)) if known_impacts else None
    metrics.codes = list(buckets.values())
    return metrics


def rank_codes(rows: list[DiscountCodeRow], *, sort: str = "amount") -> list[DiscountCodeRow]:
    if sort == "code":
        return sorted(rows, key=lambda row: (row.code == NO_CODE, row.code.casefold()))
    if sort == "orders":
        return sorted(rows, key=lambda row: (-row.orders, -row.amount, row.code.casefold()))
    if sort == "avg":
        return sorted(
            rows,
            key=lambda row: (row.average is None, -(row.average or ZERO), row.code.casefold()),
        )
    if sort == "profit":
        return sorted(
            rows,
            key=lambda row: (row.profit_impact is None, -(row.profit_impact or ZERO), row.code.casefold()),
        )
    return sorted(rows, key=lambda row: (-row.amount, -row.orders, row.code.casefold()))


def rank_discount_orders(rows: list[DiscountOrderRow], *, sort: str = "date") -> list[DiscountOrderRow]:
    if sort == "name":
        return sorted(rows, key=lambda row: row.name)
    if sort == "code":
        return sorted(rows, key=lambda row: (row.code == NO_CODE, row.code.casefold(), row.name))
    if sort == "amount":
        return sorted(rows, key=lambda row: (-row.amount, row.name))
    if sort == "total":
        return sorted(rows, key=lambda row: (-row.total_price, row.name))
    if sort == "profit":
        return sorted(
            rows,
            key=lambda row: (row.profit_impact is None, -(row.profit_impact or ZERO), row.name),
        )
    return sorted(rows, key=lambda row: (row.placed_on, row.name), reverse=True)


def monthly_discounts(metrics: DiscountMetrics, date_range: DateRange) -> list[dict]:
    series = []
    for month in date_range.months():
        amount = ZERO
        orders = 0
        for row in metrics.rows:
            if not month.contains(row.placed_on):
                continue
            amount = quantise(amount + row.amount)
            orders += 1
        series.append({"label": month.label, "amount": amount, "orders": orders})
    return series
