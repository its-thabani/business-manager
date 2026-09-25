"""A price for a product that has not sold yet.

Laura prices a new garment as printer product + postage + VAT, then a markup
(usually 40%) on that total. VAT is a real cost: it is not claimed back.
Free shipping only keeps that markup when the postage is already inside the
selling price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from django.conf import settings

from apps.core.money import ZERO, margin_pct, quantise


def _assumptions() -> dict:
    defaults = {
        "payment_fee_percent": Decimal("1.5"),
        "payment_fee_fixed": Decimal("0.20"),
        "supplier_vat_rate": Decimal("20"),
    }
    defaults.update(getattr(settings, "PROFIT_ASSUMPTIONS", {}) or {})
    return defaults


def shelf_price(amount: Decimal) -> Decimal:
    """Next shop-style price ending .99, at or above ``amount``."""
    cents = (amount * 100).to_integral_value(rounding=ROUND_CEILING)
    whole = int(cents // 100)
    candidate = Decimal(whole) + Decimal("0.99")
    if candidate < quantise(amount):
        candidate = Decimal(whole + 1) + Decimal("0.99")
    return quantise(candidate)


def _card_fee(amount: Decimal, assumptions: dict) -> Decimal:
    percent = Decimal(assumptions["payment_fee_percent"])
    fixed = Decimal(assumptions["payment_fee_fixed"])
    if amount <= 0:
        return ZERO
    return quantise(amount * percent / Decimal("100") + fixed)


@dataclass
class MarkupRow:
    markup_pct: Decimal
    exact_price: Decimal
    shelf_price: Decimal
    card_fee: Decimal
    kept: Decimal
    margin_pct: Decimal | None
    garment_if_postage_charged: Decimal | None
    kept_if_priced_low_and_free: Decimal


@dataclass
class NewProductQuote:
    name: str
    product_net: Decimal
    postage_net: Decimal
    vat_rate: Decimal
    vat: Decimal
    landed: Decimal
    markup_pct: Decimal
    customer_postage: Decimal
    fee_percent: Decimal
    fee_fixed: Decimal
    rows: list[MarkupRow]

    @property
    def recommended(self) -> MarkupRow:
        for row in self.rows:
            if row.markup_pct == self.markup_pct:
                return row
        return self.rows[len(self.rows) // 2]


def quote_new_product(
    *,
    name: str,
    product_net: Decimal,
    postage_net: Decimal,
    markup_pct: Decimal = Decimal("40"),
    customer_postage: Decimal = Decimal("4.99"),
    vat_rate: Decimal | None = None,
) -> NewProductQuote:
    """Price one new garment. Nothing is written to the books."""
    assumptions = _assumptions()
    rate = Decimal(vat_rate if vat_rate is not None else assumptions["supplier_vat_rate"])
    net = quantise(product_net + postage_net)
    vat = quantise(net * rate / Decimal("100")) if net > 0 and rate > 0 else ZERO
    landed = quantise(net + vat)
    postage_charged = quantise(max(ZERO, customer_postage))

    marks = [markup_pct - Decimal("10"), markup_pct, markup_pct + Decimal("10")]
    rows = []
    for mark in marks:
        if mark < 0:
            continue
        exact = quantise(landed * (Decimal("1") + mark / Decimal("100")))
        shelf = shelf_price(exact)
        fee = _card_fee(shelf, assumptions)
        kept = quantise(shelf - landed - fee)
        garment = quantise(shelf - postage_charged)
        if garment < 0:
            garment = None
            trap = kept
        else:
            # Same garment price, but the customer is not charged postage.
            trap_take = garment
            trap_fee = _card_fee(trap_take, assumptions)
            trap = quantise(trap_take - landed - trap_fee)
        rows.append(
            MarkupRow(
                markup_pct=quantise(mark),
                exact_price=exact,
                shelf_price=shelf,
                card_fee=fee,
                kept=kept,
                margin_pct=margin_pct(kept, shelf),
                garment_if_postage_charged=garment,
                kept_if_priced_low_and_free=trap,
            )
        )

    return NewProductQuote(
        name=(name or "New product").strip() or "New product",
        product_net=quantise(product_net),
        postage_net=quantise(postage_net),
        vat_rate=quantise(rate),
        vat=vat,
        landed=landed,
        markup_pct=quantise(markup_pct),
        customer_postage=postage_charged,
        fee_percent=Decimal(assumptions["payment_fee_percent"]),
        fee_fixed=Decimal(assumptions["payment_fee_fixed"]),
        rows=rows,
    )
