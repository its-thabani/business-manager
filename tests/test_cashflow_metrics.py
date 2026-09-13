"""Tests for the financial calculation engine.

The important behaviour here is the difference between the two modes: the legacy
mode must reproduce the spreadsheet's sign-based logic exactly, and the standard
mode must treat refunds as reductions rather than as income or expenditure.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.analytics.cashflow import (
    Mode,
    category_breakdown,
    classification_differences,
    compare_periods,
    compute_cash_metrics,
    monthly_series,
)
from apps.core.periods import DateRange
from apps.finance.models import BankTransaction, Category

JUNE = DateRange(date(2026, 6, 1), date(2026, 6, 30))


@pytest.fixture
def books(seeded, make_txn):
    """A small set of books covering every category kind."""
    c = lambda name: Category.objects.get(name=name)  # noqa: E731
    make_txn("500.00", counterparty="Stripe", category=c("Shopify Payout"), when=date(2026, 6, 2))
    make_txn("100.00", counterparty="Customer", category=c("Direct Sales Income"), when=date(2026, 6, 3))
    make_txn("-200.00", counterparty="Inkthreadable", category=c("Apparel"), when=date(2026, 6, 4))
    make_txn("-30.00", counterparty="Royal Mail", category=c("Postage"), when=date(2026, 6, 5))
    make_txn("-10.00", counterparty="Fees", category=c("Payments Fees"), when=date(2026, 6, 6))
    make_txn("-25.00", counterparty="Shopify", category=c("Store Hosting"), when=date(2026, 6, 7))
    make_txn("-50.00", counterparty="Wages", category=c("Salary"), when=date(2026, 6, 8))
    make_txn("-20.00", counterparty="Tithe", category=c("Tithe"), when=date(2026, 6, 9))
    # Outside the profit and loss entirely.
    make_txn("-2000.00", counterparty="Sole Trader Transfer", category=c("Account Migration"), when=date(2026, 6, 10))
    make_txn("-500.00", counterparty="Personal Loan", category=c("EXCLUDE"), when=date(2026, 6, 11))


@pytest.mark.django_db
class TestStandardMode:
    def test_revenue_counts_only_revenue_categories(self, books):
        assert compute_cash_metrics(JUNE).revenue == Decimal("600.00")

    def test_direct_costs_are_summed_as_positive_magnitudes(self, books):
        m = compute_cash_metrics(JUNE)
        assert (m.cost_of_goods, m.shipping_costs, m.payment_fees) == (
            Decimal("200.00"), Decimal("30.00"), Decimal("10.00")
        )
        assert m.direct_costs == Decimal("240.00")

    def test_contribution_profit_excludes_overheads(self, books):
        assert compute_cash_metrics(JUNE).contribution_profit == Decimal("360.00")

    def test_operating_profit_excludes_owner_drawings(self, books):
        # 600 - 240 - 25 = 335
        assert compute_cash_metrics(JUNE).operating_profit == Decimal("335.00")

    def test_net_profit_includes_owner_drawings(self, books):
        # 600 - 240 - 25 - 70 = 265
        assert compute_cash_metrics(JUNE).net_profit == Decimal("265.00")

    def test_profit_excluding_distributions_matches_the_spreadsheet_concept(self, books):
        assert compute_cash_metrics(JUNE).net_profit_excluding_distributions == Decimal("335.00")

    def test_transfers_and_excluded_rows_do_not_affect_profit(self, books):
        m = compute_cash_metrics(JUNE)
        assert m.excluded_count == 2
        assert m.net_profit == Decimal("265.00")

    def test_margins(self, books):
        m = compute_cash_metrics(JUNE)
        assert m.contribution_margin_pct == Decimal("60.00")
        assert m.net_margin_pct == Decimal("44.17")

    def test_margin_is_unknown_rather_than_zero_when_there_is_no_revenue(self, seeded):
        assert compute_cash_metrics(JUNE).net_margin_pct is None


@pytest.mark.django_db
class TestRefundHandling:
    def test_a_customer_refund_reduces_revenue(self, seeded, make_txn):
        make_txn("100.00", category=Category.objects.get(name="Direct Sales Income"), when=date(2026, 6, 1))
        make_txn("-30.00", category=Category.objects.get(name="Sales Refund"), when=date(2026, 6, 2))
        m = compute_cash_metrics(JUNE)
        assert m.revenue == Decimal("70.00")
        assert m.refunds == Decimal("30.00")
        # The refund must not appear as an expense as well.
        assert m.operating_expenses == Decimal("0.00")

    def test_a_supplier_credit_reduces_cost_of_goods(self, seeded, make_txn):
        apparel = Category.objects.get(name="Apparel")
        make_txn("-100.00", counterparty="Inkthreadable", category=apparel, when=date(2026, 6, 1))
        make_txn("21.73", counterparty="Inkthreadable", category=apparel, when=date(2026, 6, 2))
        m = compute_cash_metrics(JUNE)
        assert m.cost_of_goods == Decimal("78.27")
        # ...and is not counted as revenue, which is what the spreadsheet did.
        assert m.revenue == Decimal("0.00")

    def test_legacy_mode_shows_the_old_treatment_of_a_supplier_credit(self, seeded, make_txn):
        apparel = Category.objects.get(name="Apparel")
        make_txn("-100.00", counterparty="Inkthreadable", category=apparel, when=date(2026, 6, 1))
        make_txn("21.73", counterparty="Inkthreadable", category=apparel, when=date(2026, 6, 2))
        legacy = compute_cash_metrics(JUNE, mode=Mode.LEGACY)
        assert legacy.revenue == Decimal("21.73")


@pytest.mark.django_db
class TestLegacyMode:
    def test_every_credit_counts_as_revenue(self, books):
        # Including the -50 Salary row's sign counterpart: only credits here are
        # the two revenue rows, so revenue matches.
        assert compute_cash_metrics(JUNE, mode=Mode.LEGACY).revenue == Decimal("600.00")

    def test_a_personal_credit_inflates_revenue(self, seeded, make_txn):
        make_txn("30.00", counterparty="Laura Sibanda", category=Category.objects.get(name="Salary"))
        legacy = compute_cash_metrics(JUNE, mode=Mode.LEGACY)
        standard = compute_cash_metrics(JUNE, mode=Mode.STANDARD)
        assert legacy.revenue == Decimal("30.00")
        assert standard.revenue == Decimal("0.00")

    def test_excluded_labels_are_dropped_by_name(self, books):
        m = compute_cash_metrics(JUNE, mode=Mode.LEGACY)
        # 200 + 30 + 10 + 25 + 50 + 20 = 335, with the 2000 and 500 excluded.
        assert m.total_expenses == Decimal("335.00")

    def test_expenses_excluding_distributions(self, books):
        m = compute_cash_metrics(JUNE, mode=Mode.LEGACY)
        assert m.total_expenses_excluding_distributions == Decimal("265.00")

    def test_uncategorised_rows_are_still_counted(self, seeded, make_txn):
        # The spreadsheet's filters only excluded two named labels, so a blank
        # category passed through and was counted.
        make_txn("40.00", counterparty="Mystery")
        assert compute_cash_metrics(JUNE, mode=Mode.LEGACY).revenue == Decimal("40.00")

    def test_uncategorised_rows_are_excluded_from_the_corrected_figures(self, seeded, make_txn):
        make_txn("40.00", counterparty="Mystery")
        m = compute_cash_metrics(JUNE)
        assert m.revenue == Decimal("0.00")
        assert m.uncategorised_count == 1
        assert m.uncategorised_value == Decimal("40.00")
        assert m.has_data_quality_warnings


@pytest.mark.django_db
class TestDateBoundaries:
    def test_both_end_dates_are_included(self, seeded, make_txn):
        make_txn("10.00", category=Category.objects.get(name="Income"), when=date(2026, 6, 1))
        make_txn("10.00", category=Category.objects.get(name="Income"), when=date(2026, 6, 30))
        make_txn("99.00", category=Category.objects.get(name="Income"), when=date(2026, 7, 1))
        assert compute_cash_metrics(JUNE).revenue == Decimal("20.00")

    def test_empty_period_returns_zeroes_not_errors(self, seeded):
        m = compute_cash_metrics(DateRange(date(2030, 1, 1), date(2030, 1, 31)))
        assert m.revenue == Decimal("0.00")
        assert m.net_profit == Decimal("0.00")
        assert m.transaction_count == 0


@pytest.mark.django_db
class TestBreakdownsAndSeries:
    def test_category_breakdown_percentages_sum_to_one_hundred(self, books):
        rows = category_breakdown(BankTransaction.objects.in_range(JUNE))
        assert sum(r["pct_of_total"] for r in rows) == pytest.approx(Decimal("100"), abs=Decimal("0.5"))

    def test_breakdown_is_ordered_by_magnitude(self, books):
        rows = category_breakdown(BankTransaction.objects.in_range(JUNE))
        magnitudes = [r["magnitude"] for r in rows]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_expenses_only_breakdown_omits_revenue_and_transfers(self, books):
        rows = category_breakdown(BankTransaction.objects.in_range(JUNE), expenses_only=True)
        names = {r["name"] for r in rows}
        assert "Shopify Payout" not in names
        assert "Account Migration" not in names
        assert "Apparel" in names

    def test_monthly_series_returns_one_entry_per_month(self, books):
        series = monthly_series(DateRange(date(2026, 1, 1), date(2026, 12, 31)))
        assert len(series) == 12
        june = next(m for m in series if m["label"] == "Jun 2026")
        assert june["revenue"] == Decimal("600.00")


@pytest.mark.django_db
class TestComparisons:
    def test_previous_period_comparison(self, seeded, make_txn):
        income = Category.objects.get(name="Income")
        make_txn("100.00", category=income, when=date(2026, 5, 15))
        make_txn("150.00", category=income, when=date(2026, 6, 15))
        comparison = compare_periods(JUNE)
        assert comparison.current.revenue == Decimal("150.00")
        assert comparison.previous.revenue == Decimal("100.00")
        assert comparison.change("revenue") == Decimal("50.00")
        assert comparison.delta("revenue") == Decimal("50.00")

    def test_last_year_comparison(self, seeded, make_txn):
        income = Category.objects.get(name="Income")
        make_txn("80.00", category=income, when=date(2025, 6, 15))
        make_txn("120.00", category=income, when=date(2026, 6, 15))
        comparison = compare_periods(JUNE, against="last_year")
        assert comparison.previous.revenue == Decimal("80.00")
        assert comparison.change("revenue") == Decimal("50.00")

    def test_change_against_an_empty_period_is_unknown(self, seeded, make_txn):
        make_txn("150.00", category=Category.objects.get(name="Income"), when=date(2026, 6, 15))
        assert compare_periods(JUNE).change("revenue") is None


@pytest.mark.django_db
class TestSpreadsheetClassificationGap:
    def test_salary_credits_and_supplier_refunds_explain_the_workbook_gap(self, seeded, make_txn):
        income = Category.objects.get(name="Income")
        salary = Category.objects.get(name="Salary")
        apparel = Category.objects.get(name="Apparel")
        make_txn("3778.51", category=income, when=date(2026, 6, 2))
        make_txn("-2575.69", category=apparel, when=date(2026, 6, 4))
        make_txn("21.73", category=apparel, counterparty="Inkthreadable", when=date(2026, 8, 29))
        make_txn("24.00", category=salary, counterparty="Laura Sibanda & Thabani Sibanda", when=date(2026, 8, 14))
        make_txn("30.00", category=salary, counterparty="Laura Sibanda", when=date(2026, 8, 16))
        make_txn("-482.00", category=Category.objects.get(name="Tithe"), when=date(2026, 2, 3))

        ytd = DateRange(date(2026, 1, 1), date(2026, 9, 13))
        standard = compute_cash_metrics(ytd)
        legacy = compute_cash_metrics(ytd, mode=Mode.LEGACY)
        diffs = classification_differences(ytd)

        assert standard.net_profit == legacy.net_profit
        assert legacy.revenue - standard.revenue == Decimal("75.73")
        assert legacy.total_expenses_excluding_distributions - standard.total_expenses_excluding_distributions == Decimal("21.73")
        assert legacy.net_profit_excluding_distributions - standard.net_profit_excluding_distributions == Decimal("54.00")
        assert {row.amount for row in diffs} == {Decimal("21.73"), Decimal("24.00"), Decimal("30.00")}
