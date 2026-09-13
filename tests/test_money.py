from decimal import Decimal

import pytest

from apps.core.money import fmt, margin_pct, pct_change, safe_divide, to_decimal


class TestToDecimal:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("12.34", "12.34"),
            ("-12.34", "-12.34"),
            ("£1,234.56", "1234.56"),
            ("  £ 1,234.56 ", "1234.56"),
            ("(45.00)", "-45.00"),  # accounting-style negative
            (12.3, "12.30"),
            (12, "12.00"),
            (Decimal("1.005"), "1.01"),  # half-up, not banker's rounding
            ("1.005", "1.01"),
        ],
    )
    def test_parses_common_formats(self, raw, expected):
        assert to_decimal(raw) == Decimal(expected)

    @pytest.mark.parametrize("raw", ["", None, "   ", "abc", "-", ".", True, False])
    def test_returns_default_for_unusable_values(self, raw):
        assert to_decimal(raw) is None

    def test_float_does_not_leak_binary_error(self):
        # Decimal(0.1) would be 0.1000000000000000055511151231257827
        assert to_decimal(0.1) == Decimal("0.10")

    def test_large_sums_stay_exact(self):
        total = sum((to_decimal("0.01") for _ in range(1000)), Decimal("0"))
        assert total == Decimal("10.00")


class TestDerivedFigures:
    def test_safe_divide_returns_none_rather_than_zero_for_no_denominator(self):
        # None means "not available"; zero would wrongly imply a real measurement.
        assert safe_divide(Decimal("10"), Decimal("0")) is None
        assert safe_divide(Decimal("10"), None) is None

    def test_margin_pct(self):
        assert margin_pct(Decimal("48.20"), Decimal("100.00")) == Decimal("48.20")

    def test_margin_pct_with_no_revenue_is_unknown(self):
        assert margin_pct(Decimal("10"), Decimal("0")) is None

    def test_pct_change_uses_absolute_previous_so_a_loss_reads_correctly(self):
        # Going from -100 to -50 is a 50% improvement, not -50%.
        assert pct_change(Decimal("-50"), Decimal("-100")) == Decimal("50.00")

    def test_pct_change_from_zero_is_undefined(self):
        assert pct_change(Decimal("10"), Decimal("0")) is None


class TestFormatting:
    def test_unknown_renders_as_dash_not_zero(self):
        assert fmt(None) == "—"

    def test_negative_sign_precedes_currency_symbol(self):
        assert fmt(Decimal("-1234.5")) == "-£1,234.50"

    def test_thousands_separator(self):
        assert fmt(Decimal("1234567.89")) == "£1,234,567.89"
