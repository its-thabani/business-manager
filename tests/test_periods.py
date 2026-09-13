from datetime import date

import pytest

from apps.core.periods import DateRange, preset_range, requested_range


class TestDateRange:
    def test_range_is_inclusive_of_both_ends(self):
        # Matches how the spreadsheet's start/end inputs behave.
        assert DateRange(date(2026, 1, 1), date(2026, 1, 31)).days == 31

    def test_rejects_inverted_range(self):
        with pytest.raises(ValueError):
            DateRange(date(2026, 2, 1), date(2026, 1, 1))

    def test_previous_period_is_same_length_and_does_not_overlap(self):
        current = DateRange(date(2026, 6, 1), date(2026, 6, 30))
        previous = current.previous_period()
        assert previous.days == current.days
        assert previous.end == date(2026, 5, 31)
        assert previous.start == date(2026, 5, 2)

    def test_same_period_last_year(self):
        current = DateRange(date(2026, 1, 1), date(2026, 9, 7))
        last_year = current.same_period_last_year()
        assert (last_year.start, last_year.end) == (date(2025, 1, 1), date(2025, 9, 7))

    def test_leap_day_shifts_back_to_28_february(self):
        shifted = DateRange(date(2024, 2, 29), date(2024, 2, 29)).same_period_last_year()
        assert shifted.start == date(2023, 2, 28)

    def test_months_are_clipped_to_the_range(self):
        months = DateRange(date(2026, 1, 15), date(2026, 3, 10)).months()
        assert [(m.start, m.end) for m in months] == [
            (date(2026, 1, 15), date(2026, 1, 31)),
            (date(2026, 2, 1), date(2026, 2, 28)),
            (date(2026, 3, 1), date(2026, 3, 10)),
        ]

    def test_single_month_range_yields_one_month(self):
        assert len(DateRange(date(2026, 5, 3), date(2026, 5, 4)).months()) == 1


class TestPresets:
    def test_30d_includes_today(self):
        r = preset_range("30d", today=date(2026, 9, 13))
        assert (r.start, r.end) == (date(2026, 8, 15), date(2026, 9, 13))
        assert r.days == 30

    def test_ytd_starts_in_january(self):
        r = preset_range("ytd", today=date(2026, 9, 13))
        assert (r.start, r.end) == (date(2026, 1, 1), date(2026, 9, 13))

    def test_last_month_covers_the_whole_previous_month(self):
        r = preset_range("last_month", today=date(2026, 3, 5))
        assert (r.start, r.end) == (date(2026, 2, 1), date(2026, 2, 28))

    def test_quarter_to_date(self):
        r = preset_range("qtd", today=date(2026, 8, 20))
        assert (r.start, r.end) == (date(2026, 7, 1), date(2026, 8, 20))

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError):
            preset_range("nonsense")


class TestRequestedRange:
    def test_custom_dates_win_over_a_preset(self):
        window, key = requested_range({"range": "ytd", "start": "2026-03-01", "end": "2026-03-31"})
        assert key == "custom"
        assert (window.start, window.end) == (date(2026, 3, 1), date(2026, 3, 31))

    def test_inverted_custom_dates_are_swapped(self):
        window, key = requested_range({"start": "2026-03-31", "end": "2026-03-01"})
        assert key == "custom"
        assert (window.start, window.end) == (date(2026, 3, 1), date(2026, 3, 31))

    def test_invalid_custom_dates_fall_back_to_the_preset(self):
        window, key = requested_range({"range": "mtd", "start": "nope", "end": "2026-03-01"}, today=date(2026, 9, 13))
        assert key == "mtd"
        assert window.start == date(2026, 9, 1)
