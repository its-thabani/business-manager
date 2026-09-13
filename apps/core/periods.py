"""Date-range handling for reporting periods.

Ranges are inclusive of both ``start`` and ``end`` dates, matching how the
existing Finance Dashboard treats its start/end inputs, so figures produced here
can be compared directly against the spreadsheet.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class DateRange:
    """An inclusive date range."""

    start: date
    end: date
    label: str = ""

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"start {self.start} is after end {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, when: date) -> bool:
        return self.start <= when <= self.end

    def previous_period(self) -> DateRange:
        """The immediately preceding range of equal length.

        Used for "vs previous period" comparisons where a like-for-like window
        matters more than aligning to calendar boundaries.
        """
        length = self.days
        new_end = self.start - timedelta(days=1)
        return DateRange(new_end - timedelta(days=length - 1), new_end, label=f"Previous {length} days")

    def same_period_last_year(self) -> DateRange:
        """The equivalent range shifted back twelve months.

        This mirrors the spreadsheet's ``EDATE(-12)`` "Last Year" comparison.
        """
        return DateRange(
            self.start - relativedelta(years=1),
            self.end - relativedelta(years=1),
            label="Same period last year",
        )

    def months(self) -> list[DateRange]:
        """Split into calendar months, clipped to the range boundaries."""
        out: list[DateRange] = []
        cursor = self.start.replace(day=1)
        while cursor <= self.end:
            last_day = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
            out.append(
                DateRange(
                    max(cursor, self.start),
                    min(last_day, self.end),
                    label=cursor.strftime("%b %Y"),
                )
            )
            cursor += relativedelta(months=1)
        return out


# --------------------------------------------------------------------------
# Named presets
# --------------------------------------------------------------------------

PRESETS = {
    "today": "Today",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "mtd": "This month",
    "last_month": "Last month",
    "qtd": "This quarter",
    "ytd": "Year to date",
    "last_year": "Last calendar year",
    "12m": "Last 12 months",
    "all": "All time",
}


def preset_range(key: str, *, today: date | None = None, earliest: date | None = None) -> DateRange:
    """Resolve a preset key such as ``"30d"`` into a concrete ``DateRange``.

    ``earliest`` is only consulted for the ``all`` preset, where the range must
    extend back to the first record actually held.
    """
    today = today or date.today()

    if key == "today":
        return DateRange(today, today, PRESETS[key])
    if key in {"7d", "30d", "90d"}:
        days = int(key.rstrip("d"))
        return DateRange(today - timedelta(days=days - 1), today, PRESETS[key])
    if key == "mtd":
        return DateRange(today.replace(day=1), today, PRESETS[key])
    if key == "last_month":
        first_of_this = today.replace(day=1)
        end = first_of_this - timedelta(days=1)
        return DateRange(end.replace(day=1), end, PRESETS[key])
    if key == "qtd":
        first_month = 3 * ((today.month - 1) // 3) + 1
        return DateRange(today.replace(month=first_month, day=1), today, PRESETS[key])
    if key == "ytd":
        return DateRange(today.replace(month=1, day=1), today, PRESETS[key])
    if key == "last_year":
        year = today.year - 1
        return DateRange(date(year, 1, 1), date(year, 12, 31), PRESETS[key])
    if key == "12m":
        return DateRange(today - relativedelta(years=1) + timedelta(days=1), today, PRESETS[key])
    if key == "all":
        return DateRange(earliest or date(2020, 1, 1), today, PRESETS[key])

    raise ValueError(f"Unknown date range preset: {key!r}")


def parse_iso_date(raw: str | None) -> date | None:
    """Parse ``YYYY-MM-DD``. Invalid or blank values become ``None``."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def requested_range(params, *, earliest: date | None = None, today: date | None = None) -> tuple[DateRange, str]:
    """Resolve ``range=`` / ``start=`` / ``end=`` from a query-string mapping.

    Custom dates win when both ends are valid. An inverted pair is swapped
    rather than rejected, so a mistyped form still shows a real window.
    """
    start = parse_iso_date(params.get("start") if hasattr(params, "get") else None)
    end = parse_iso_date(params.get("end") if hasattr(params, "get") else None)
    if start and end:
        if start > end:
            start, end = end, start
        return DateRange(start, end, f"{start:%d %b %Y} – {end:%d %b %Y}"), "custom"
    preset = (params.get("range") if hasattr(params, "get") else None) or "ytd"
    if preset not in PRESETS:
        preset = "ytd"
    return preset_range(preset, today=today, earliest=earliest), preset
