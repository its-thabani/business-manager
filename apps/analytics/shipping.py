"""Shipping charged to the customer versus supplier postage.

Free postage on a physical order is a real decision. A £0 download is not —
those lines do not require shipping. Supplier cost is only used when an
Inkthreadable order records postage; otherwise the figure stays unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.core.money import ZERO, quantise, safe_divide
from apps.core.periods import DateRange
from apps.sales.models import Order


def _is_shippable(order) -> bool:
    return any(line.requires_shipping for line in order.lines.all())


def _supplier_shipping(order) -> Decimal | None:
    """Inkthreadable postage when recorded. Partial rows stay unknown."""
    costs = [so.shipping_cost for so in order.supplier_orders.all()]
    known = [quantise(cost) for cost in costs if cost is not None]
    if not known:
        return None
    if len(known) != len(costs):
        return None
    return quantise(sum(known, ZERO))


@dataclass
class ShippingOrderRow:
    order_id: int
    name: str
    placed_on: date
    country: str
    charged: Decimal
    cost: Decimal | None
    is_shippable: bool

    @property
    def is_free(self) -> bool:
        return self.is_shippable and self.charged == ZERO

    @property
    def net(self) -> Decimal | None:
        if self.cost is None:
            return None
        return quantise(self.charged - self.cost)


@dataclass
class CountryRow:
    country: str
    orders: int = 0
    free_orders: int = 0
    charged: Decimal = ZERO
    cost: Decimal | None = None
    cost_missing: int = 0
    charged_on_known: Decimal = ZERO

    @property
    def net(self) -> Decimal | None:
        if self.cost is None:
            return None
        return quantise(self.charged_on_known - self.cost)


@dataclass
class ShippingMetrics:
    date_range: DateRange
    shippable: int = 0
    digital: int = 0
    paid_postage: int = 0
    free_postage: int = 0
    charged: Decimal = ZERO
    cost: Decimal | None = None
    cost_known_orders: int = 0
    cost_missing: int = 0
    charged_on_known: Decimal = ZERO
    free_cost: Decimal | None = None
    free_cost_known: int = 0
    free_cost_missing: int = 0
    rows: list[ShippingOrderRow] = field(default_factory=list)
    countries: list[CountryRow] = field(default_factory=list)

    @property
    def net(self) -> Decimal | None:
        if self.cost is None:
            return None
        return quantise(self.charged_on_known - self.cost)

    @property
    def coverage_pct(self) -> Decimal | None:
        value = (
            safe_divide(Decimal(self.cost_known_orders) * 100, Decimal(self.shippable))
            if self.shippable
            else None
        )
        return quantise(value) if value is not None else None

    @property
    def free_rate_pct(self) -> Decimal | None:
        value = (
            safe_divide(Decimal(self.free_postage) * 100, Decimal(self.shippable))
            if self.shippable
            else None
        )
        return quantise(value) if value is not None else None


def compute_shipping(date_range: DateRange) -> ShippingMetrics:
    """Postage charged vs supplier cost for countable orders in ``date_range``."""
    metrics = ShippingMetrics(date_range=date_range)
    countries: dict[str, CountryRow] = {}
    known_costs: list[Decimal] = []
    free_costs: list[Decimal] = []

    orders = (
        Order.objects.countable()
        .in_range(date_range)
        .prefetch_related("lines", "supplier_orders")
        .order_by("placed_on", "pk")
    )
    for order in orders:
        shippable = _is_shippable(order)
        if not shippable:
            metrics.digital += 1
            continue

        charged = quantise(order.shipping_charged or ZERO)
        cost = _supplier_shipping(order)
        country = (order.shipping_country or "").strip().upper() or "Unknown"
        row = ShippingOrderRow(
            order_id=order.pk,
            name=order.name or f"Order {order.pk}",
            placed_on=order.placed_on,
            country=country,
            charged=charged,
            cost=cost,
            is_shippable=True,
        )
        metrics.rows.append(row)
        metrics.shippable += 1
        metrics.charged = quantise(metrics.charged + charged)
        if charged == ZERO:
            metrics.free_postage += 1
        else:
            metrics.paid_postage += 1

        bucket = countries.get(country)
        if bucket is None:
            bucket = CountryRow(country=country)
            countries[country] = bucket
        bucket.orders += 1
        bucket.charged = quantise(bucket.charged + charged)
        if row.is_free:
            bucket.free_orders += 1

        if cost is None:
            metrics.cost_missing += 1
            bucket.cost_missing += 1
            if row.is_free:
                metrics.free_cost_missing += 1
        else:
            metrics.cost_known_orders += 1
            metrics.charged_on_known = quantise(metrics.charged_on_known + charged)
            known_costs.append(cost)
            bucket.charged_on_known = quantise(bucket.charged_on_known + charged)
            bucket.cost = quantise((bucket.cost or ZERO) + cost)
            if row.is_free:
                metrics.free_cost_known += 1
                free_costs.append(cost)

    metrics.cost = quantise(sum(known_costs, ZERO)) if known_costs else None
    metrics.free_cost = quantise(sum(free_costs, ZERO)) if free_costs else None
    metrics.countries = list(countries.values())
    return metrics


def rank_countries(rows: list[CountryRow], *, sort: str = "charged") -> list[CountryRow]:
    if sort == "country":
        return sorted(rows, key=lambda row: (row.country == "Unknown", row.country))
    if sort == "orders":
        return sorted(rows, key=lambda row: (-row.orders, -row.charged, row.country))
    if sort == "free":
        return sorted(rows, key=lambda row: (-row.free_orders, -row.orders, row.country))
    if sort == "cost":
        return sorted(
            rows,
            key=lambda row: (row.cost is None, -(row.cost or ZERO), row.country),
        )
    if sort == "net":
        return sorted(
            rows,
            key=lambda row: (row.net is None, -(row.net or ZERO), row.country),
        )
    return sorted(rows, key=lambda row: (-row.charged, -row.orders, row.country))


def rank_shipping_orders(rows: list[ShippingOrderRow], *, sort: str = "date") -> list[ShippingOrderRow]:
    if sort == "name":
        return sorted(rows, key=lambda row: row.name)
    if sort == "country":
        return sorted(rows, key=lambda row: (row.country == "Unknown", row.country, row.name))
    if sort == "charged":
        return sorted(rows, key=lambda row: (-row.charged, row.name))
    if sort == "cost":
        return sorted(
            rows,
            key=lambda row: (row.cost is None, -(row.cost or ZERO), row.name),
        )
    if sort == "net":
        return sorted(
            rows,
            key=lambda row: (row.net is None, -(row.net or ZERO), row.name),
        )
    return sorted(rows, key=lambda row: (row.placed_on, row.name), reverse=True)


def monthly_shipping(metrics: ShippingMetrics, date_range: DateRange) -> list[dict]:
    """Charged postage and known supplier cost per calendar month."""
    series = []
    for month in date_range.months():
        charged = ZERO
        costs: list[Decimal] = []
        free = 0
        for row in metrics.rows:
            if not month.contains(row.placed_on):
                continue
            charged = quantise(charged + row.charged)
            if row.cost is not None:
                costs.append(row.cost)
            if row.is_free:
                free += 1
        series.append(
            {
                "label": month.label,
                "charged": charged,
                "cost": quantise(sum(costs, ZERO)) if costs else ZERO,
                "free": free,
            }
        )
    return series
