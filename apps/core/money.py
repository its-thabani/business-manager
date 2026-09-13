"""Money handling.

Every monetary value in this application is a ``Decimal`` quantised to two
decimal places. Floats are never used for money because binary floating point
cannot represent decimal currency amounts exactly, which produces drift once
thousands of transactions are summed.

Sign convention, applied consistently everywhere:

    positive = money into the business
    negative = money out of the business

This mirrors how the bank presents a statement, so an imported row never needs
its sign reinterpreted.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def to_decimal(value: object, default: Decimal | None = None) -> Decimal | None:
    """Coerce an arbitrary spreadsheet/CSV/API value to a 2dp ``Decimal``.

    Returns ``default`` for blanks and unparseable values rather than raising,
    because import sources routinely contain empty cells. Callers that require a
    value should pass ``default=None`` and check the result.
    """
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return quantise(value)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return quantise(Decimal(value))
    if isinstance(value, float):
        # str() first: Decimal(float) would capture the float's binary error.
        return quantise(Decimal(str(value)))

    text = str(value).strip()
    if not text:
        return default

    # Strip currency symbols, thousands separators and accounting parentheses.
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    for junk in ("£", "$", "€", ",", "\u00a0", " "):
        text = text.replace(junk, "")
    if not text or text in {"-", "."}:
        return default

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return default
    return quantise(-amount if negative else amount)


def quantise(value: Decimal) -> Decimal:
    """Round to 2dp using half-up, the convention used for currency."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def safe_divide(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """Divide, returning ``None`` when the result is not meaningful.

    A ``None`` result means "not available" and must be surfaced as such rather
    than displayed as zero, which would imply a real measurement of nil.
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def margin_pct(profit: Decimal | None, revenue: Decimal | None) -> Decimal | None:
    """Profit as a percentage of revenue, or ``None`` when there is no revenue."""
    ratio = safe_divide(profit, revenue)
    if ratio is None:
        return None
    return quantise(ratio * 100)


def pct_change(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    """Percentage change between two periods.

    Returns ``None`` when the previous period is zero or missing, since the
    change is then undefined rather than infinite.
    """
    if current is None or previous is None or previous == 0:
        return None
    return quantise((Decimal(current) - Decimal(previous)) / abs(Decimal(previous)) * 100)


def fmt(value: Decimal | None, *, currency: str = "£", dash: str = "—") -> str:
    """Render a money value for display, using an em dash for unknown values."""
    if value is None:
        return dash
    value = quantise(Decimal(value))
    sign = "-" if value < 0 else ""
    return f"{sign}{currency}{abs(value):,.2f}"
