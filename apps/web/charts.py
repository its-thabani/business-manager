"""Turn calculated series into heights and widths for the dashboard charts.

The arithmetic lives in ``apps.analytics``. This only scales numbers so a
template can draw bars. Heights are percentages of the largest magnitude in
the series, which is how the spreadsheet's monthly columns read at a glance.
"""

from __future__ import annotations

from decimal import Decimal

from apps.core.money import ZERO


def column_chart(rows: list[dict], keys: list[str]) -> list[dict]:
    """Add ``<key>_pct`` height percentages for each series key."""
    peak = ZERO
    for row in rows:
        for key in keys:
            peak = max(peak, abs(Decimal(row.get(key) or 0)))
    if peak == 0:
        peak = Decimal("1")

    out = []
    for row in rows:
        item = dict(row)
        for key in keys:
            value = Decimal(row.get(key) or 0)
            item[f"{key}_pct"] = float(abs(value) / peak * 100)
            item[f"{key}_neg"] = value < 0
        out.append(item)
    return out


def line_chart(
    rows: list[dict],
    keys: list[str],
    *,
    width: int = 640,
    height: int = 168,
    pad: int = 22,
) -> dict:
    """SVG coordinates for one or more series. Zero is not assumed to be the floor."""
    values = [Decimal(row.get(key) or 0) for row in rows for key in keys]
    loft = max(values, default=ZERO)
    floor = min(values, default=ZERO)
    span = loft - floor
    if span == 0:
        span = Decimal("1")
        floor = loft - span
    inner_w = width - pad * 2
    inner_h = height - pad * 2
    count = max(len(rows) - 1, 1)
    series = {}
    for key in keys:
        points = []
        for index, row in enumerate(rows):
            value = Decimal(row.get(key) or 0)
            x = pad + (inner_w * index / count)
            y = pad + inner_h - ((value - floor) / span * inner_h)
            points.append(
                {
                    "x": round(float(x), 2),
                    "y": round(float(y), 2),
                    "value": value,
                    "label": row.get("label", ""),
                }
            )
        series[key] = {
            "points": points,
            "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
        }
    zero_y = pad + inner_h - ((ZERO - floor) / span * inner_h)
    return {
        "width": width,
        "height": height,
        "series": series,
        "zero_y": round(float(zero_y), 2),
        "rows": rows,
    }


def share_bars(rows: list[dict], *, value_key: str = "units", limit: int = 10) -> list[dict]:
    """Top-N rows with a width percentage of the largest value."""
    ranked = sorted(rows, key=lambda row: Decimal(row.get(value_key) or 0), reverse=True)[:limit]
    peak = max((Decimal(row.get(value_key) or 0) for row in ranked), default=Decimal("1"))
    if peak == 0:
        peak = Decimal("1")
    out = []
    for row in ranked:
        item = dict(row)
        value = Decimal(row.get(value_key) or 0)
        item["bar_pct"] = float(value / peak * 100)
        out.append(item)
    return out


def pie_slices(
    rows: list[dict],
    *,
    value_key: str = "revenue",
    colour_key: str = "colour",
) -> list[dict]:
    """Conic-gradient stops: each slice is a share of the sum of values."""
    total = sum((Decimal(row.get(value_key) or 0) for row in rows), ZERO)
    if total <= 0:
        return []
    cursor = ZERO
    out = []
    for row in rows:
        value = Decimal(row.get(value_key) or 0)
        if value <= 0:
            continue
        share = value / total * 100
        start = cursor
        cursor += share
        item = dict(row)
        item["value"] = value
        item["share_pct"] = float(share)
        item["start_pct"] = float(start)
        item["end_pct"] = float(cursor)
        item["colour"] = row.get(colour_key) or "#94a3b8"
        out.append(item)
    if out:
        out[-1]["end_pct"] = 100.0
    return out
