"""Refund rates by product and variant.

The clock is the sale, not the refund date: of what was sold in the period, how
much came back. Cash given back is Shopify's refund total. Line amounts that
disagree with that total are used only as a share, never as extra money.
Print-on-demand cost is not treated as recovered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.core.money import ZERO, quantise, safe_divide
from apps.core.periods import DateRange
from apps.sales.models import Order

UNALLOCATED = "Unallocated"


def _is_digital(order) -> bool:
    lines = list(order.lines.all())
    return bool(lines) and not any(line.requires_shipping for line in lines)


def _product_label(line) -> str:
    if line.product_id and line.product:
        return line.product.title
    return line.title or "Unknown product"


def _variant_label(line) -> str:
    product = _product_label(line)
    extra = (line.variant_title or "").strip()
    if line.variant_id and line.variant:
        extra = extra or (line.variant.title or "").strip()
    return f"{product} · {extra}" if extra else product


def _allocate_refund(refund) -> tuple[list[tuple], Decimal]:
    """Share the refund's cash across its lines. Leftover cash is unallocated."""
    lines = list(refund.lines.all())
    cash = quantise(refund.amount or ZERO)
    if not lines:
        return [], cash
    weights = [quantise(item.amount or ZERO) for item in lines]
    weight_sum = quantise(sum(weights, ZERO))
    if weight_sum == ZERO:
        qty = sum((item.quantity or ZERO for item in lines), ZERO)
        shares = []
        for item in lines:
            units = item.quantity or ZERO
            share = quantise(cash * units / qty) if qty else ZERO
            shares.append((item.order_line, units, share))
        return shares, ZERO
    shares = []
    for item, weight in zip(lines, weights, strict=True):
        shares.append(
            (item.order_line, item.quantity or ZERO, quantise(cash * weight / weight_sum))
        )
    return shares, ZERO


@dataclass
class RefundItemRow:
    key: str
    label: str
    product_id: int | None
    sold_units: Decimal = ZERO
    refunded_units: Decimal = ZERO
    amount: Decimal = ZERO
    refunded_orders: int = 0
    _order_ids: set[int] = field(default_factory=set, repr=False)

    @property
    def rate_pct(self) -> Decimal | None:
        value = (
            safe_divide(self.refunded_units * 100, self.sold_units) if self.sold_units else None
        )
        return quantise(value) if value is not None else None


@dataclass
class RefundEventRow:
    refund_id: int
    order_id: int
    name: str
    placed_on: date
    refunded_on: date
    amount: Decimal
    units: Decimal
    products: str
    product_ids: list[int] = field(default_factory=list)


@dataclass
class RefundMetrics:
    date_range: DateRange
    hide_digital: bool
    universe: int = 0
    digital: int = 0
    sold_units: Decimal = ZERO
    refunded_orders: int = 0
    refunded_units: Decimal = ZERO
    amount: Decimal = ZERO
    unallocated: Decimal = ZERO
    sales: Decimal = ZERO
    rows: list[RefundEventRow] = field(default_factory=list)
    products: list[RefundItemRow] = field(default_factory=list)
    variants: list[RefundItemRow] = field(default_factory=list)

    @property
    def order_rate_pct(self) -> Decimal | None:
        value = (
            safe_divide(Decimal(self.refunded_orders) * 100, Decimal(self.universe))
            if self.universe
            else None
        )
        return quantise(value) if value is not None else None

    @property
    def unit_rate_pct(self) -> Decimal | None:
        value = (
            safe_divide(self.refunded_units * 100, self.sold_units) if self.sold_units else None
        )
        return quantise(value) if value is not None else None

    @property
    def of_sales_pct(self) -> Decimal | None:
        value = safe_divide(self.amount * 100, self.sales) if self.sales else None
        return quantise(value) if value is not None else None


def compute_refunds(date_range: DateRange, *, hide_digital: bool = True) -> RefundMetrics:
    """Refunds against countable orders placed in ``date_range``."""
    metrics = RefundMetrics(date_range=date_range, hide_digital=hide_digital)
    products: dict[str, RefundItemRow] = {}
    variants: dict[str, RefundItemRow] = {}

    orders = (
        Order.objects.countable()
        .in_range(date_range)
        .prefetch_related(
            "lines",
            "lines__product",
            "lines__variant",
            "refunds",
            "refunds__lines",
            "refunds__lines__order_line",
            "refunds__lines__order_line__product",
            "refunds__lines__order_line__variant",
        )
        .order_by("placed_on", "pk")
    )
    for order in orders:
        if _is_digital(order):
            metrics.digital += 1
            if hide_digital:
                continue
        metrics.universe += 1
        metrics.sales = quantise(metrics.sales + (order.total_price or ZERO))
        for line in order.lines.all():
            units = Decimal(line.quantity)
            metrics.sold_units += units
            _touch(products, f"p:{line.product_id or line.pk}", _product_label(line), line.product_id).sold_units += units
            _touch(
                variants,
                f"v:{line.variant_id or line.pk}",
                _variant_label(line),
                line.product_id,
            ).sold_units += units

        refunds = list(order.refunds.all())
        if refunds:
            metrics.refunded_orders += 1
        for refund in refunds:
            cash = quantise(refund.amount or ZERO)
            metrics.amount = quantise(metrics.amount + cash)
            shares, leftover = _allocate_refund(refund)
            metrics.unallocated = quantise(metrics.unallocated + leftover)
            units = sum((share[1] for share in shares), ZERO)
            metrics.refunded_units += units
            labels = []
            for line, qty, share in shares:
                labels.append(_product_label(line))
                product = _touch(
                    products, f"p:{line.product_id or line.pk}", _product_label(line), line.product_id
                )
                variant = _touch(
                    variants,
                    f"v:{line.variant_id or line.pk}",
                    _variant_label(line),
                    line.product_id,
                )
                product.refunded_units += qty
                variant.refunded_units += qty
                product.amount = quantise(product.amount + share)
                variant.amount = quantise(variant.amount + share)
                product._order_ids.add(order.pk)
                variant._order_ids.add(order.pk)
            if leftover and leftover > ZERO:
                bucket = _touch(products, "unallocated", UNALLOCATED, None)
                bucket.amount = quantise(bucket.amount + leftover)
                bucket._order_ids.add(order.pk)
            metrics.rows.append(
                RefundEventRow(
                    refund_id=refund.pk,
                    order_id=order.pk,
                    name=order.name or f"Order {order.pk}",
                    placed_on=order.placed_on,
                    refunded_on=refund.refunded_on,
                    amount=cash,
                    units=units,
                    products=", ".join(dict.fromkeys(labels)) or UNALLOCATED,
                    product_ids=list(
                        dict.fromkeys(line.product_id for line, _, _ in shares if line.product_id)
                    ),
                )
            )

    for row in products.values():
        row.refunded_orders = len(row._order_ids)
    for row in variants.values():
        row.refunded_orders = len(row._order_ids)
    metrics.products = list(products.values())
    metrics.variants = list(variants.values())
    return metrics


def _touch(buckets: dict[str, RefundItemRow], key: str, label: str, product_id: int | None) -> RefundItemRow:
    row = buckets.get(key)
    if row is None:
        row = RefundItemRow(key=key, label=label, product_id=product_id)
        buckets[key] = row
    return row


def filter_refund_items(rows: list[RefundItemRow], *, hide_zero: bool) -> list[RefundItemRow]:
    if not hide_zero:
        return list(rows)
    return [row for row in rows if row.refunded_units or row.amount]


def rank_refund_items(rows: list[RefundItemRow], *, sort: str = "rate") -> list[RefundItemRow]:
    if sort == "name":
        return sorted(rows, key=lambda row: (row.label == UNALLOCATED, row.label.casefold()))
    if sort == "units":
        return sorted(rows, key=lambda row: (-row.sold_units, row.label.casefold()))
    if sort == "refunded":
        return sorted(rows, key=lambda row: (-row.refunded_units, -row.amount, row.label.casefold()))
    if sort == "amount":
        return sorted(rows, key=lambda row: (-row.amount, -row.refunded_units, row.label.casefold()))
    return sorted(
        rows,
        key=lambda row: (row.rate_pct is None, -(row.rate_pct or ZERO), -row.refunded_units, row.label.casefold()),
    )


def rank_refund_events(rows: list[RefundEventRow], *, sort: str = "date") -> list[RefundEventRow]:
    if sort == "name":
        return sorted(rows, key=lambda row: row.name)
    if sort == "amount":
        return sorted(rows, key=lambda row: (-row.amount, row.name))
    if sort == "units":
        return sorted(rows, key=lambda row: (-row.units, row.name))
    if sort == "placed":
        return sorted(rows, key=lambda row: (row.placed_on, row.name), reverse=True)
    return sorted(rows, key=lambda row: (row.refunded_on, row.name), reverse=True)


def monthly_refunds(metrics: RefundMetrics, date_range: DateRange) -> list[dict]:
    series = []
    for month in date_range.months():
        amount = ZERO
        count = 0
        for row in metrics.rows:
            if not month.contains(row.placed_on):
                continue
            amount = quantise(amount + row.amount)
            count += 1
        series.append({"label": month.label, "amount": amount, "refunds": count})
    return series
