"""Bank-derived financial metrics.

This is the backend replacement for the ``Dashboard``, ``Monthly Report`` and
``Expense Summary`` sheets. All arithmetic happens here, in Python and SQL, so
results are deterministic and reproducible.

These figures describe **cash movement through the bank account**, which is a
different thing from Shopify sales. A Shopify order raises revenue on the day it
is placed; the money lands days later, net of card fees, and may be partly
refunded afterwards. Order-level revenue and profit are computed separately from
Shopify and supplier data; reconciling the two is what the Reconciliation section
exists to do.

Two calculation modes are provided:

``STANDARD``
    Classifies money by the *kind* of its category. Refunds reduce the figure
    they belong to: a customer refund reduces revenue, and a supplier credit
    reduces cost of goods sold.

``LEGACY``
    Reproduces the spreadsheet's logic exactly — every positive amount is
    revenue and every negative amount is an expense, regardless of category.
    Retained so the imported data can be proved to match the old figures to the
    penny before anything is trusted, and so the difference between the two
    treatments can be quantified rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, QuerySet, Sum

from apps.core.money import ZERO, margin_pct, quantise
from apps.core.periods import DateRange
from apps.finance.models import BankTransaction, CategoryKind

# Category *names* the spreadsheet filtered out by name rather than by meaning.
LEGACY_EXCLUDED_LABELS = ("Account Migration", "EXCLUDE")


class Mode:
    STANDARD = "standard"
    LEGACY = "legacy"


def _sum(queryset: QuerySet, condition: Q) -> Decimal:
    """Sum the signed amount of matching rows, treating no rows as zero."""
    total = queryset.filter(condition).aggregate(total=Sum("amount"))["total"]
    return quantise(total) if total is not None else ZERO


@dataclass
class CashMetrics:
    """Financial metrics for one date range, derived from bank transactions."""

    date_range: DateRange
    mode: str

    revenue: Decimal = ZERO
    refunds: Decimal = ZERO

    cost_of_goods: Decimal = ZERO
    shipping_costs: Decimal = ZERO
    payment_fees: Decimal = ZERO
    operating_expenses: Decimal = ZERO
    distributions: Decimal = ZERO

    transaction_count: int = 0
    uncategorised_count: int = 0
    uncategorised_value: Decimal = ZERO
    excluded_count: int = 0

    by_category: list[dict] = field(default_factory=list)

    # -- Derived -----------------------------------------------------------

    @property
    def direct_costs(self) -> Decimal:
        """Costs attributable to fulfilling sales."""
        return quantise(self.cost_of_goods + self.shipping_costs + self.payment_fees)

    @property
    def contribution_profit(self) -> Decimal:
        """Revenue less the costs of delivering it, before overheads."""
        return quantise(self.revenue - self.direct_costs)

    @property
    def operating_profit(self) -> Decimal:
        """Contribution profit less overheads. Excludes owner drawings."""
        return quantise(self.contribution_profit - self.operating_expenses)

    @property
    def total_expenses(self) -> Decimal:
        """Every outgoing that counts against profit, including drawings."""
        return quantise(self.direct_costs + self.operating_expenses + self.distributions)

    @property
    def total_expenses_excluding_distributions(self) -> Decimal:
        """Mirrors the spreadsheet's "Expenses (exc. salary & tithe)"."""
        return quantise(self.direct_costs + self.operating_expenses)

    @property
    def net_profit(self) -> Decimal:
        """Profit after everything, including owner drawings."""
        return quantise(self.revenue - self.total_expenses)

    @property
    def net_profit_excluding_distributions(self) -> Decimal:
        """Mirrors the spreadsheet's "Profit (exc. salary & tithe)"."""
        return quantise(self.revenue - self.total_expenses_excluding_distributions)

    @property
    def net_margin_pct(self) -> Decimal | None:
        return margin_pct(self.net_profit, self.revenue)

    @property
    def net_margin_excluding_distributions_pct(self) -> Decimal | None:
        return margin_pct(self.net_profit_excluding_distributions, self.revenue)

    @property
    def contribution_margin_pct(self) -> Decimal | None:
        return margin_pct(self.contribution_profit, self.revenue)

    @property
    def has_data_quality_warnings(self) -> bool:
        return self.uncategorised_count > 0

    def as_dict(self) -> dict:
        """Flat representation for templates, JSON responses and comparisons."""
        return {
            "start": self.date_range.start,
            "end": self.date_range.end,
            "label": self.date_range.label,
            "mode": self.mode,
            "revenue": self.revenue,
            "refunds": self.refunds,
            "cost_of_goods": self.cost_of_goods,
            "shipping_costs": self.shipping_costs,
            "payment_fees": self.payment_fees,
            "direct_costs": self.direct_costs,
            "contribution_profit": self.contribution_profit,
            "contribution_margin_pct": self.contribution_margin_pct,
            "operating_expenses": self.operating_expenses,
            "operating_profit": self.operating_profit,
            "distributions": self.distributions,
            "total_expenses": self.total_expenses,
            "total_expenses_excluding_distributions": self.total_expenses_excluding_distributions,
            "net_profit": self.net_profit,
            "net_profit_excluding_distributions": self.net_profit_excluding_distributions,
            "net_margin_pct": self.net_margin_pct,
            "net_margin_excluding_distributions_pct": self.net_margin_excluding_distributions_pct,
            "transaction_count": self.transaction_count,
            "uncategorised_count": self.uncategorised_count,
            "uncategorised_value": self.uncategorised_value,
        }


def compute_cash_metrics(
    date_range: DateRange,
    *,
    mode: str = Mode.STANDARD,
    queryset: QuerySet | None = None,
) -> CashMetrics:
    """Compute bank-derived metrics for ``date_range``."""
    base = (queryset if queryset is not None else BankTransaction.objects.all()).in_range(date_range)
    metrics = CashMetrics(date_range=date_range, mode=mode)

    if mode == Mode.LEGACY:
        return _compute_legacy(base, metrics)
    return _compute_standard(base, metrics)


def _compute_standard(base: QuerySet, metrics: CashMetrics) -> CashMetrics:
    reportable = base.reportable()

    revenue_rows = Q(category__kind=CategoryKind.REVENUE)
    # Revenue is the net of money in and refunds out, since a refund is stored as
    # a negative amount in a revenue category.
    metrics.revenue = _sum(reportable, revenue_rows)
    metrics.refunds = -_sum(reportable, revenue_rows & Q(amount__lt=0))

    # Costs are negated so they read as positive magnitudes. A positive amount in
    # a cost category is a supplier credit, and correctly reduces the cost.
    metrics.cost_of_goods = -_sum(reportable, Q(category__kind=CategoryKind.COGS))
    metrics.shipping_costs = -_sum(reportable, Q(category__kind=CategoryKind.SHIPPING))
    metrics.payment_fees = -_sum(reportable, Q(category__kind=CategoryKind.FEES))
    metrics.operating_expenses = -_sum(reportable, Q(category__kind=CategoryKind.OPERATING))
    metrics.distributions = -_sum(reportable, Q(category__kind=CategoryKind.DISTRIBUTION))

    metrics.transaction_count = reportable.count()
    uncategorised = base.filter(category__isnull=True).aggregate(n=Count("pk"), total=Sum("amount"))
    metrics.uncategorised_count = uncategorised["n"] or 0
    metrics.uncategorised_value = quantise(uncategorised["total"]) if uncategorised["total"] else ZERO
    metrics.excluded_count = base.filter(category__kind__in=CategoryKind.outside_pnl()).count()
    metrics.by_category = category_breakdown(reportable)
    return metrics


def _compute_legacy(base: QuerySet, metrics: CashMetrics) -> CashMetrics:
    """Reproduce the spreadsheet: sign decides revenue vs expense."""
    included = base.exclude(category__name__in=LEGACY_EXCLUDED_LABELS)

    metrics.revenue = _sum(included, Q(amount__gt=0))
    # Everything negative is an expense, allocated to the buckets the spreadsheet
    # would have reported it under.
    metrics.distributions = -_sum(
        included, Q(amount__lt=0) & Q(category__kind=CategoryKind.DISTRIBUTION)
    )
    everything_out = -_sum(included, Q(amount__lt=0))
    metrics.operating_expenses = quantise(everything_out - metrics.distributions)
    metrics.cost_of_goods = ZERO
    metrics.shipping_costs = ZERO
    metrics.payment_fees = ZERO

    metrics.transaction_count = base.count()
    uncategorised = base.filter(category__isnull=True).aggregate(n=Count("pk"), total=Sum("amount"))
    metrics.uncategorised_count = uncategorised["n"] or 0
    metrics.uncategorised_value = quantise(uncategorised["total"]) if uncategorised["total"] else ZERO
    metrics.excluded_count = base.filter(category__name__in=LEGACY_EXCLUDED_LABELS).count()
    metrics.by_category = category_breakdown(base)
    return metrics


def category_breakdown(queryset: QuerySet, *, expenses_only: bool = False) -> list[dict]:
    """Totals per category, ordered by magnitude.

    Replaces the ``Expense Summary`` sheet. Percentages are of the total spend
    within the returned set, so they always add up to 100. Transfers and
    excluded categories never appear — they are outside the books.
    """
    rows = (
        queryset.exclude(category__isnull=True)
        .exclude(category__kind__in=CategoryKind.outside_pnl())
        .values("category__name", "category__kind", "category__colour")
        .annotate(total=Sum("amount"), count=Count("pk"))
        .order_by("category__sort_order", "category__name")
    )

    out = []
    for row in rows:
        total = quantise(row["total"] or ZERO)
        kind = row["category__kind"]
        if expenses_only and kind == CategoryKind.REVENUE:
            continue
        out.append(
            {
                "name": row["category__name"],
                "kind": kind,
                "colour": row["category__colour"],
                "total": total,
                # Magnitude of spend, so a cost reads as a positive number.
                "magnitude": abs(total),
                "count": row["count"],
            }
        )

    spend = sum(r["magnitude"] for r in out) or ZERO
    for row in out:
        row["pct_of_total"] = (
            quantise(row["magnitude"] / spend * 100) if spend else None
        )
    return sorted(out, key=lambda r: r["magnitude"], reverse=True)


_EXPENSE_KINDS = (
    CategoryKind.COGS,
    CategoryKind.SHIPPING,
    CategoryKind.FEES,
    CategoryKind.OPERATING,
    CategoryKind.DISTRIBUTION,
)


@dataclass
class ExpenseAnalysis:
    """Every reportable outgoing in a period — the old Expense Summary sheet."""

    date_range: DateRange
    include_drawings: bool
    total: Decimal = ZERO
    count: int = 0
    refunds_out: Decimal = ZERO
    refunds_count: int = 0
    uncategorised_count: int = 0
    uncategorised_value: Decimal = ZERO
    categories: list[dict] = field(default_factory=list)
    transactions: list = field(default_factory=list)


def expense_analysis(
    date_range: DateRange,
    *,
    include_drawings: bool = True,
    queryset: QuerySet | None = None,
) -> ExpenseAnalysis:
    """Money that left the account and counts as a true expense.

    Excluded and transfer rows are omitted. Customer refunds are listed
    separately: they are money out, but they reduce sales rather than adding
    to spend. Uncategorised minuses are shown so they can be labelled, and
    are not added to the expense total.
    """
    base = (queryset if queryset is not None else BankTransaction.objects.all()).in_range(date_range)
    reportable = base.reportable()
    kinds = list(_EXPENSE_KINDS)
    if not include_drawings:
        kinds.remove(CategoryKind.DISTRIBUTION)

    outgoings = (
        reportable.filter(amount__lt=0, category__kind__in=kinds)
        .select_related("category", "account")
        .order_by("-occurred_on", "amount")
    )
    refunds = reportable.filter(amount__lt=0, category__kind=CategoryKind.REVENUE)
    uncategorised = base.filter(category__isnull=True, amount__lt=0)

    result = ExpenseAnalysis(date_range=date_range, include_drawings=include_drawings)
    result.total = -_sum(outgoings, Q())
    result.count = outgoings.count()
    result.refunds_out = -_sum(refunds, Q())
    result.refunds_count = refunds.count()
    result.uncategorised_count = uncategorised.count()
    uncat_total = uncategorised.aggregate(total=Sum("amount"))["total"]
    result.uncategorised_value = quantise(uncat_total) if uncat_total is not None else ZERO
    result.categories = category_breakdown(outgoings, expenses_only=True)
    result.transactions = list(outgoings)
    return result


def monthly_series(date_range: DateRange, *, mode: str = Mode.STANDARD) -> list[dict]:
    """Month-by-month revenue, expenses and profit. Replaces ``Monthly Report``."""
    series = []
    for month in date_range.months():
        metrics = compute_cash_metrics(month, mode=mode)
        series.append(
            {
                "label": month.label,
                "start": month.start,
                "end": month.end,
                "revenue": metrics.revenue,
                "expenses": metrics.total_expenses,
                "expenses_excluding_distributions": metrics.total_expenses_excluding_distributions,
                "profit": metrics.net_profit,
                "profit_excluding_distributions": metrics.net_profit_excluding_distributions,
                "margin_pct": metrics.net_margin_pct,
            }
        )
    return series


@dataclass
class PeriodComparison:
    """A metric set alongside a comparison period."""

    current: CashMetrics
    previous: CashMetrics

    def change(self, attribute: str) -> Decimal | None:
        from apps.core.money import pct_change

        return pct_change(getattr(self.current, attribute), getattr(self.previous, attribute))

    def delta(self, attribute: str) -> Decimal:
        return quantise(getattr(self.current, attribute) - getattr(self.previous, attribute))


@dataclass
class ClassificationDifference:
    """A bank row the spreadsheet and this system treat differently."""

    occurred_on: date
    amount: Decimal
    category: str
    counterparty: str
    spreadsheet_as: str
    this_system_as: str
    why: str


def classification_differences(
    date_range: DateRange,
    *,
    queryset: QuerySet | None = None,
) -> list[ClassificationDifference]:
    """Credits the spreadsheet counted as income that are not sales, and refunds
    it counted as expenses.

    The two modes agree on net profit. They disagree on revenue, expenses, and
    profit excluding salary & tithe whenever a non-sale credit or a sales refund
    appears in the period.
    """
    from apps.finance.models import BankTransaction

    base = (queryset if queryset is not None else BankTransaction.objects.all()).in_range(date_range)
    included = base.exclude(category__name__in=LEGACY_EXCLUDED_LABELS).exclude(category__isnull=True)
    out: list[ClassificationDifference] = []

    for txn in included.filter(amount__gt=0).exclude(category__kind=CategoryKind.REVENUE).select_related("category"):
        kind = txn.category.kind
        if kind == CategoryKind.DISTRIBUTION:
            system_as = "owner drawings (reduces salary/tithe, not income)"
            why = "Money coming back from a salary/tithe category is not a sale."
        elif kind == CategoryKind.COGS:
            system_as = "reduces cost of goods"
            why = "A supplier refund is a cost credit, not revenue."
        elif kind == CategoryKind.SHIPPING:
            system_as = "reduces shipping cost"
            why = "A postage refund reduces postage, not sales."
        elif kind == CategoryKind.FEES:
            system_as = "reduces fees"
            why = "A fee refund reduces fees, not sales."
        elif kind == CategoryKind.OPERATING:
            system_as = "reduces operating expenses"
            why = "A credit on an overhead is not a sale."
        else:
            continue
        out.append(
            ClassificationDifference(
                occurred_on=txn.occurred_on,
                amount=quantise(txn.amount),
                category=txn.category.name,
                counterparty=txn.counterparty,
                spreadsheet_as="revenue",
                this_system_as=system_as,
                why=why,
            )
        )

    for txn in included.filter(amount__lt=0, category__kind=CategoryKind.REVENUE).select_related("category"):
        out.append(
            ClassificationDifference(
                occurred_on=txn.occurred_on,
                amount=quantise(txn.amount),
                category=txn.category.name,
                counterparty=txn.counterparty,
                spreadsheet_as="expense",
                this_system_as="reduces revenue",
                why="A customer refund is not an operating cost.",
            )
        )

    out.sort(key=lambda row: (row.occurred_on, row.category, row.amount))
    return out


def compare_periods(
    date_range: DateRange,
    *,
    against: str = "previous",
    mode: str = Mode.STANDARD,
) -> PeriodComparison:
    """Compute metrics for a range and its comparison period.

    ``against`` is either ``"previous"`` for the preceding window of equal
    length, or ``"last_year"`` for the same dates twelve months earlier, which is
    the comparison the existing dashboard shows.
    """
    comparison_range = (
        date_range.same_period_last_year() if against == "last_year" else date_range.previous_period()
    )
    return PeriodComparison(
        current=compute_cash_metrics(date_range, mode=mode),
        previous=compute_cash_metrics(comparison_range, mode=mode),
    )
