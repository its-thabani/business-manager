"""Customer analytics from Shopify orders.

New vs returning is decided by the customer's first paid order, not by how many
times they bought inside the selected window. A guest checkout with no customer
record and no email is counted as unidentified and excluded from rates rather
than invented as a person.

Free £0 downloads are excluded by default: they are lead magnets, not sales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.core.money import ZERO, quantise, safe_divide
from apps.core.periods import DateRange
from apps.sales.models import Order


def identity_key(order) -> str | None:
    """Stable id for a buyer. Customer record wins over email."""
    if order.customer_id:
        return f"c:{order.customer_id}"
    email = (order.email or "").strip().casefold()
    if email:
        return f"e:{email}"
    return None


@dataclass
class CustomerRow:
    key: str
    customer_id: int | None
    name: str
    email: str
    country: str
    first_paid_on: date
    last_paid_on: date
    lifetime_orders: int
    lifetime_spend: Decimal
    period_orders: int
    period_spend: Decimal
    is_new: bool
    order_dates: list[date] = field(default_factory=list)

    @property
    def is_returning(self) -> bool:
        return self.period_orders > 0 and not self.is_new

    @property
    def repeated_in_period(self) -> bool:
        return self.period_orders >= 2


@dataclass
class CustomerMetrics:
    date_range: DateRange
    paid_only: bool
    identified: int = 0
    unidentified_orders: int = 0
    new_customers: int = 0
    returning_customers: int = 0
    repeaters_in_period: int = 0
    paid_orders: int = 0
    period_spend: Decimal = ZERO
    spend_new: Decimal = ZERO
    spend_returning: Decimal = ZERO
    rows: list[CustomerRow] = field(default_factory=list)

    @property
    def aov(self) -> Decimal | None:
        value = safe_divide(self.period_spend, Decimal(self.paid_orders)) if self.paid_orders else None
        return quantise(value) if value is not None else None

    @property
    def aov_new(self) -> Decimal | None:
        orders = sum(row.period_orders for row in self.rows if row.is_new)
        value = safe_divide(self.spend_new, Decimal(orders)) if orders else None
        return quantise(value) if value is not None else None

    @property
    def aov_returning(self) -> Decimal | None:
        orders = sum(row.period_orders for row in self.rows if row.is_returning)
        value = safe_divide(self.spend_returning, Decimal(orders)) if orders else None
        return quantise(value) if value is not None else None

    @property
    def repeat_rate_pct(self) -> Decimal | None:
        """Share of period customers whose first paid order was earlier."""
        value = (
            safe_divide(Decimal(self.returning_customers) * 100, Decimal(self.identified))
            if self.identified
            else None
        )
        return quantise(value) if value is not None else None

    @property
    def in_period_repeat_pct(self) -> Decimal | None:
        """Share of period customers who placed two or more orders in the window."""
        value = (
            safe_divide(Decimal(self.repeaters_in_period) * 100, Decimal(self.identified))
            if self.identified
            else None
        )
        return quantise(value) if value is not None else None


def _base_orders(*, paid_only: bool):
    qs = Order.objects.countable().select_related("customer")
    if paid_only:
        qs = qs.exclude(total_price=0)
    return qs


def compute_customer_metrics(date_range: DateRange, *, paid_only: bool = True) -> CustomerMetrics:
    """Identify buyers in ``date_range`` and classify them as new or returning."""
    metrics = CustomerMetrics(date_range=date_range, paid_only=paid_only)
    buckets: dict[str, CustomerRow] = {}

    for order in _base_orders(paid_only=paid_only).order_by("placed_on", "pk"):
        key = identity_key(order)
        if key is None:
            if date_range.contains(order.placed_on):
                metrics.unidentified_orders += 1
                if paid_only or order.total_price:
                    metrics.paid_orders += 1
                    metrics.period_spend = quantise(metrics.period_spend + (order.total_price or ZERO))
            continue

        customer = order.customer
        name = str(customer) if customer else (order.email or "Customer")
        email = (customer.email if customer and customer.email else order.email) or ""
        country = (customer.country if customer and customer.country else order.shipping_country) or ""
        spend = order.total_price or ZERO

        row = buckets.get(key)
        if row is None:
            row = CustomerRow(
                key=key,
                customer_id=order.customer_id,
                name=name,
                email=email,
                country=country,
                first_paid_on=order.placed_on,
                last_paid_on=order.placed_on,
                lifetime_orders=0,
                lifetime_spend=ZERO,
                period_orders=0,
                period_spend=ZERO,
                is_new=False,
            )
            buckets[key] = row
        row.lifetime_orders += 1
        row.lifetime_spend = quantise(row.lifetime_spend + spend)
        row.last_paid_on = order.placed_on
        row.order_dates.append(order.placed_on)
        if not row.email and email:
            row.email = email
        if not row.country and country:
            row.country = country
        if date_range.contains(order.placed_on):
            row.period_orders += 1
            row.period_spend = quantise(row.period_spend + spend)
            metrics.paid_orders += 1
            metrics.period_spend = quantise(metrics.period_spend + spend)

    period_rows = []
    for row in buckets.values():
        if row.period_orders == 0:
            continue
        row.is_new = date_range.contains(row.first_paid_on)
        period_rows.append(row)
        if row.is_new:
            metrics.new_customers += 1
            metrics.spend_new = quantise(metrics.spend_new + row.period_spend)
        else:
            metrics.returning_customers += 1
            metrics.spend_returning = quantise(metrics.spend_returning + row.period_spend)
        if row.repeated_in_period:
            metrics.repeaters_in_period += 1

    metrics.identified = len(period_rows)
    metrics.rows = period_rows
    return metrics


def rank_customers(rows: list[CustomerRow], *, sort: str = "spend") -> list[CustomerRow]:
    if sort == "orders":
        return sorted(rows, key=lambda row: (-row.period_orders, -row.period_spend, row.name.casefold()))
    if sort == "last":
        return sorted(rows, key=lambda row: (row.last_paid_on, row.name.casefold()), reverse=True)
    if sort == "first":
        return sorted(rows, key=lambda row: (row.first_paid_on, row.name.casefold()), reverse=True)
    if sort == "name":
        return sorted(rows, key=lambda row: row.name.casefold())
    return sorted(rows, key=lambda row: (-row.period_spend, -row.period_orders, row.name.casefold()))


def monthly_customer_mix(metrics: CustomerMetrics, date_range: DateRange) -> list[dict]:
    """New vs returning buyers per calendar month, by first paid-order date."""
    series = []
    for month in date_range.months():
        new = returning = 0
        for row in metrics.rows:
            ordered_here = any(month.contains(day) for day in row.order_dates)
            if not ordered_here:
                continue
            if month.contains(row.first_paid_on):
                new += 1
            else:
                returning += 1
        series.append({"label": month.label, "new": new, "returning": returning})
    return series
