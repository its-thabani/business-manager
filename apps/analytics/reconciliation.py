"""Reconcile Shopify sales and supplier invoices to bank cash.

The matcher only links a bank row when the amount and date actually line up.
It never invents a match, and it never overwrites a link you have confirmed.

Shopify payouts are identified by counterparty (Stripe, Adyen) rather than
category name: most live rows are still labelled "Income" from the spreadsheet.
Inkthreadable charges are identified the same way.

A daily expected cash figure (net sales minus card fees) is matched to the
earliest unused payout inside a short window. Leftover payouts are then tried
against consecutive unmatched sales days, which is how multi-day Stripe batches
show up. Supplier invoices are matched the same way, amount first, then date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.analytics.profitability import compute_order_profits, summarise
from apps.core.money import ZERO, quantise
from apps.core.periods import DateRange
from apps.finance.models import (
    BankTransaction,
    CashLink,
    CashLinkConfidence,
    CashLinkKind,
)
from apps.supplier.models import SupplierOrder

AMOUNT_TOLERANCE = Decimal("0.05")
SHOPIFY_LAG_MIN = -2
SHOPIFY_LAG_MAX = 10
SUPPLIER_WINDOW_DAYS = 14

PAYOUT_COUNTERPARTIES = ("stripe", "adyen")
SUPPLIER_COUNTERPARTIES = ("inkthreadable", "hyper merch")
PAYOUT_CATEGORY_NAMES = {"shopify payout"}


def amounts_close(left: Decimal, right: Decimal, *, tolerance: Decimal = AMOUNT_TOLERANCE) -> bool:
    return abs(quantise(left) - quantise(right)) <= tolerance


def _name(transaction: BankTransaction) -> str:
    return (transaction.counterparty or "").casefold()


def is_payout(transaction: BankTransaction) -> bool:
    """Stripe / Adyen money in, or a row explicitly categorised as a Shopify payout."""
    if transaction.amount <= 0:
        return False
    if any(token in _name(transaction) for token in PAYOUT_COUNTERPARTIES):
        return True
    category = getattr(transaction, "category", None)
    return bool(category and category.name.casefold() in PAYOUT_CATEGORY_NAMES)


def is_supplier_charge(transaction: BankTransaction) -> bool:
    if transaction.amount >= 0:
        return False
    return any(token in _name(transaction) for token in SUPPLIER_COUNTERPARTIES)


@dataclass
class SaleDay:
    sale_on: date
    net_sales: Decimal = ZERO
    payment_fees: Decimal = ZERO
    expected_cash: Decimal = ZERO
    order_count: int = 0


@dataclass
class SuggestedLink:
    """A proposed or already-saved connection between a bank row and its source."""

    bank_transaction: BankTransaction
    kind: str
    expected_amount: Decimal
    reason: str
    sale_dates: list[date] = field(default_factory=list)
    supplier_order: SupplierOrder | None = None
    confidence: str = CashLinkConfidence.SUGGESTED
    persisted: CashLink | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.confidence == CashLinkConfidence.CONFIRMED

    @property
    def sale_on(self) -> date | None:
        return self.sale_dates[0] if self.sale_dates else None

    @property
    def difference(self) -> Decimal:
        return quantise(self.bank_transaction.amount - self.expected_amount)

    @property
    def extra_sale_dates(self) -> list[str]:
        return [d.isoformat() for d in self.sale_dates[1:]]


@dataclass
class ReconciliationReport:
    date_range: DateRange
    shopify_net_sales: Decimal = ZERO
    shopify_fees: Decimal = ZERO
    expected_cash: Decimal = ZERO
    bank_received: Decimal = ZERO
    cash_gap: Decimal = ZERO

    estimated_fulfilment: Decimal | None = None
    fulfilment_coverage_pct: Decimal | None = None
    supplier_invoices: Decimal | None = None
    supplier_invoice_count: int = 0
    supplier_bank: Decimal = ZERO
    supplier_gap: Decimal | None = None
    invoice_span: str = ""

    shopify_matches: list[SuggestedLink] = field(default_factory=list)
    unmatched_days: list[SaleDay] = field(default_factory=list)
    unmatched_payouts: list[BankTransaction] = field(default_factory=list)
    supplier_matches: list[SuggestedLink] = field(default_factory=list)
    unmatched_charges: list[BankTransaction] = field(default_factory=list)
    unmatched_invoices: list[SupplierOrder] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    months: list[dict] = field(default_factory=list)


def sale_days(date_range: DateRange | None = None, *, profits=None) -> list[SaleDay]:
    """Net sales minus card fees, grouped by the calendar day the order was placed."""
    grouped: dict[date, SaleDay] = {}
    for profit in profits if profits is not None else compute_order_profits(date_range):
        day = grouped.setdefault(profit.order.placed_on, SaleDay(sale_on=profit.order.placed_on))
        day.net_sales += profit.net_sales
        day.payment_fees += profit.payment_fee or ZERO
        day.expected_cash += profit.expected_cash
        day.order_count += 1
    for day in grouped.values():
        day.net_sales = quantise(day.net_sales)
        day.payment_fees = quantise(day.payment_fees)
        day.expected_cash = quantise(day.expected_cash)
    return sorted(grouped.values(), key=lambda row: row.sale_on)


def payout_transactions(date_range: DateRange | None = None) -> list[BankTransaction]:
    rows = BankTransaction.objects.select_related("category", "cash_link")
    if date_range is not None:
        rows = rows.in_range(date_range)
    return [row for row in rows.order_by("occurred_on", "pk") if is_payout(row)]


def supplier_charges(date_range: DateRange | None = None) -> list[BankTransaction]:
    rows = BankTransaction.objects.select_related("category", "cash_link")
    if date_range is not None:
        rows = rows.in_range(date_range)
    return [row for row in rows.order_by("occurred_on", "pk") if is_supplier_charge(row)]


def _confirmed_links() -> list[CashLink]:
    return list(
        CashLink.objects.filter(confidence=CashLinkConfidence.CONFIRMED).select_related(
            "bank_transaction",
            "bank_transaction__category",
            "supplier_order",
        )
    )


def _link_from_saved(link: CashLink) -> SuggestedLink:
    expected = link.expected_amount
    if expected is None:
        expected = abs(link.bank_transaction.amount)
    return SuggestedLink(
        bank_transaction=link.bank_transaction,
        kind=link.kind,
        expected_amount=quantise(expected),
        reason=link.match_reason or "confirmed",
        sale_dates=link.sale_dates,
        supplier_order=link.supplier_order,
        confidence=link.confidence,
        persisted=link,
    )


def _in_window(sale_on: date, paid_on: date) -> bool:
    lag = (paid_on - sale_on).days
    return SHOPIFY_LAG_MIN <= lag <= SHOPIFY_LAG_MAX


def match_shopify_days(
    days: list[SaleDay],
    payouts: list[BankTransaction],
    *,
    reserved_txn_ids: set[int] | None = None,
    reserved_dates: set[date] | None = None,
) -> list[SuggestedLink]:
    """Greedy daily match, then consecutive-day batches. Never invents a fit."""
    used_txn = set(reserved_txn_ids or ())
    used_dates = set(reserved_dates or ())
    matches: list[SuggestedLink] = []

    remaining_days = [day for day in days if day.sale_on not in used_dates]
    remaining_payouts = [row for row in payouts if row.pk not in used_txn]

    for day in remaining_days:
        candidates = [
            row
            for row in remaining_payouts
            if row.pk not in used_txn
            and _in_window(day.sale_on, row.occurred_on)
            and amounts_close(row.amount, day.expected_cash)
        ]
        if not candidates:
            continue
        pick = min(
            candidates,
            key=lambda row: (
                abs((row.occurred_on - day.sale_on).days),
                abs(row.amount - day.expected_cash),
                row.pk,
            ),
        )
        lag = (pick.occurred_on - day.sale_on).days
        matches.append(
            SuggestedLink(
                bank_transaction=pick,
                kind=CashLinkKind.SHOPIFY_DAY,
                expected_amount=day.expected_cash,
                reason=f"sales day {day.sale_on:%Y-%m-%d} within {lag} day(s), ±{AMOUNT_TOLERANCE}",
                sale_dates=[day.sale_on],
            )
        )
        used_txn.add(pick.pk)
        used_dates.add(day.sale_on)

    leftover_days = [day for day in remaining_days if day.sale_on not in used_dates]
    leftover_payouts = [row for row in remaining_payouts if row.pk not in used_txn]

    for payout in leftover_payouts:
        window_days = [
            day
            for day in leftover_days
            if day.sale_on not in used_dates and _in_window(day.sale_on, payout.occurred_on)
        ]
        batch = _consecutive_sum(window_days, payout.amount)
        if not batch:
            continue
        total = quantise(sum((day.expected_cash for day in batch), ZERO))
        labels = ", ".join(day.sale_on.strftime("%Y-%m-%d") for day in batch)
        matches.append(
            SuggestedLink(
                bank_transaction=payout,
                kind=CashLinkKind.SHOPIFY_DAY,
                expected_amount=total,
                reason=f"batch of {len(batch)} sales days ({labels}) ±{AMOUNT_TOLERANCE}",
                sale_dates=[day.sale_on for day in batch],
            )
        )
        used_txn.add(payout.pk)
        used_dates.update(day.sale_on for day in batch)

    return matches


def _consecutive_sum(days: list[SaleDay], target: Decimal) -> list[SaleDay] | None:
    """A run of two or more windowed sales days whose expected cash equals ``target``."""
    ordered = sorted(days, key=lambda day: day.sale_on)
    for start in range(len(ordered)):
        total = ZERO
        for end in range(start, len(ordered)):
            total += ordered[end].expected_cash
            if end > start and amounts_close(total, target):
                return ordered[start : end + 1]
            if total > target + AMOUNT_TOLERANCE:
                break
    return None


def match_supplier_charges(
    charges: list[BankTransaction],
    invoices: list[SupplierOrder],
    *,
    reserved_txn_ids: set[int] | None = None,
    reserved_invoice_ids: set[int] | None = None,
) -> list[SuggestedLink]:
    used_txn = set(reserved_txn_ids or ())
    used_invoices = set(reserved_invoice_ids or ())
    matches: list[SuggestedLink] = []

    usable = []
    for invoice in invoices:
        total = invoice.known_total
        placed = invoice.placed_at.date() if invoice.placed_at else None
        if total is None or placed is None or invoice.pk in used_invoices:
            continue
        usable.append((invoice, quantise(total), placed))

    for charge in charges:
        if charge.pk in used_txn:
            continue
        target = quantise(abs(charge.amount))
        candidates = [
            (invoice, total, placed)
            for invoice, total, placed in usable
            if invoice.pk not in used_invoices
            and amounts_close(total, target)
            and abs((placed - charge.occurred_on).days) <= SUPPLIER_WINDOW_DAYS
        ]
        if not candidates:
            continue
        invoice, total, placed = min(
            candidates,
            key=lambda row: (abs((row[2] - charge.occurred_on).days), row[0].pk),
        )
        lag = (charge.occurred_on - placed).days
        matches.append(
            SuggestedLink(
                bank_transaction=charge,
                kind=CashLinkKind.SUPPLIER_ORDER,
                expected_amount=quantise(-total),
                reason=f"invoice {invoice.supplier_reference} within {lag} day(s), ±{AMOUNT_TOLERANCE}",
                supplier_order=invoice,
            )
        )
        used_txn.add(charge.pk)
        used_invoices.add(invoice.pk)

    return matches


def persist_link(suggestion: SuggestedLink, *, confidence: str = CashLinkConfidence.SUGGESTED) -> CashLink:
    """Write one suggestion. Refuses to overwrite a confirmed row."""
    existing = CashLink.objects.filter(bank_transaction=suggestion.bank_transaction).first()
    if existing and existing.is_confirmed:
        return existing
    defaults = {
        "kind": suggestion.kind,
        "confidence": confidence,
        "sale_on": suggestion.sale_on,
        "extra_sale_dates": suggestion.extra_sale_dates,
        "supplier_order": suggestion.supplier_order,
        "expected_amount": suggestion.expected_amount,
        "match_reason": suggestion.reason,
    }
    if existing:
        for field_name, value in defaults.items():
            setattr(existing, field_name, value)
        existing.save()
        return existing
    return CashLink.objects.create(bank_transaction=suggestion.bank_transaction, **defaults)


def apply_suggestions() -> dict[str, int]:
    """Persist live suggestions. Confirmed links are left untouched."""
    confirmed = _confirmed_links()
    reserved_txn = {link.bank_transaction_id for link in confirmed}
    reserved_dates = {day for link in confirmed for day in link.sale_dates}
    reserved_invoices = {link.supplier_order_id for link in confirmed if link.supplier_order_id}

    shopify = match_shopify_days(
        sale_days(),
        payout_transactions(),
        reserved_txn_ids=reserved_txn,
        reserved_dates=reserved_dates,
    )
    supplier = match_supplier_charges(
        supplier_charges(),
        list(SupplierOrder.objects.all()),
        reserved_txn_ids=reserved_txn,
        reserved_invoice_ids=reserved_invoices,
    )

    written_ids = {item.bank_transaction.pk for item in shopify + supplier}
    stale = CashLink.objects.filter(confidence=CashLinkConfidence.SUGGESTED).exclude(
        bank_transaction_id__in=written_ids
    )
    deleted = stale.count()
    stale.delete()

    created = updated = skipped = 0
    for suggestion in shopify + supplier:
        before = CashLink.objects.filter(bank_transaction=suggestion.bank_transaction).first()
        if before and before.is_confirmed:
            skipped += 1
            continue
        persist_link(suggestion)
        if before:
            updated += 1
        else:
            created += 1

    return {"created": created, "updated": updated, "skipped": skipped, "deleted": deleted}


def confirm_link(link: CashLink) -> CashLink:
    link.confidence = CashLinkConfidence.CONFIRMED
    link.save(update_fields=["confidence", "updated_at"])
    return link


def confirm_suggestion(suggestion: SuggestedLink) -> CashLink:
    return persist_link(suggestion, confidence=CashLinkConfidence.CONFIRMED)


def clear_link(link: CashLink) -> None:
    link.delete()


def _sum_invoices(invoices: list[SupplierOrder]) -> Decimal | None:
    totals = [invoice.known_total for invoice in invoices if invoice.known_total is not None]
    if not totals:
        return None
    return quantise(sum(totals, ZERO))


def reconcile(date_range: DateRange) -> ReconciliationReport:
    """Period totals plus suggested matches. Missing links stay unmatched."""
    all_profits = compute_order_profits()
    all_days = sale_days(profits=all_profits)
    days = [day for day in all_days if date_range.contains(day.sale_on)]
    period_profits = [row for row in all_profits if date_range.contains(row.order.placed_on)]
    payouts = payout_transactions(date_range)
    charges = supplier_charges(date_range)
    invoices = [
        invoice
        for invoice in SupplierOrder.objects.all()
        if invoice.placed_at and date_range.contains(invoice.placed_at.date())
    ]

    # Match against the wider set so a payout just outside the period can still
    # claim a sales day that is inside it, and vice versa.
    all_payouts = payout_transactions()
    all_charges = supplier_charges()
    all_invoices = list(SupplierOrder.objects.all())

    confirmed = _confirmed_links()
    reserved_txn = {link.bank_transaction_id for link in confirmed}
    reserved_dates = {day for link in confirmed for day in link.sale_dates}
    reserved_invoices = {link.supplier_order_id for link in confirmed if link.supplier_order_id}

    confirmed_shopify = [
        _link_from_saved(link) for link in confirmed if link.kind == CashLinkKind.SHOPIFY_DAY
    ]
    confirmed_supplier = [
        _link_from_saved(link) for link in confirmed if link.kind == CashLinkKind.SUPPLIER_ORDER
    ]

    live_shopify = match_shopify_days(
        all_days,
        all_payouts,
        reserved_txn_ids=reserved_txn,
        reserved_dates=reserved_dates,
    )
    live_supplier = match_supplier_charges(
        all_charges,
        all_invoices,
        reserved_txn_ids=reserved_txn,
        reserved_invoice_ids=reserved_invoices,
    )

    # Suggested rows already persisted stay visible with their pk for confirm/clear.
    persisted_suggested = {
        link.bank_transaction_id: link
        for link in CashLink.objects.filter(confidence=CashLinkConfidence.SUGGESTED).select_related(
            "bank_transaction",
            "supplier_order",
        )
    }
    for suggestion in live_shopify + live_supplier:
        saved = persisted_suggested.get(suggestion.bank_transaction.pk)
        if saved:
            suggestion.persisted = saved

    def _touches_period(suggestion: SuggestedLink) -> bool:
        if date_range.contains(suggestion.bank_transaction.occurred_on):
            return True
        return any(date_range.contains(day) for day in suggestion.sale_dates)

    shopify_matches = [row for row in confirmed_shopify + live_shopify if _touches_period(row)]
    supplier_matches = [row for row in confirmed_supplier + live_supplier if _touches_period(row)]

    matched_dates = {day for row in shopify_matches for day in row.sale_dates}
    matched_payout_ids = {row.bank_transaction.pk for row in shopify_matches}
    matched_charge_ids = {row.bank_transaction.pk for row in supplier_matches}
    matched_invoice_ids = {
        row.supplier_order.pk for row in supplier_matches if row.supplier_order is not None
    }

    report = ReconciliationReport(date_range=date_range)
    report.shopify_net_sales = quantise(sum((day.net_sales for day in days), ZERO))
    report.shopify_fees = quantise(sum((day.payment_fees for day in days), ZERO))
    report.expected_cash = quantise(sum((day.expected_cash for day in days), ZERO))
    report.bank_received = quantise(sum((row.amount for row in payouts), ZERO))
    report.cash_gap = quantise(report.expected_cash - report.bank_received)

    report.shopify_matches = sorted(
        shopify_matches, key=lambda row: row.bank_transaction.occurred_on, reverse=True
    )
    report.unmatched_days = [day for day in days if day.sale_on not in matched_dates]
    report.unmatched_payouts = [row for row in payouts if row.pk not in matched_payout_ids]

    report.supplier_matches = sorted(
        supplier_matches, key=lambda row: row.bank_transaction.occurred_on, reverse=True
    )
    report.unmatched_charges = [row for row in charges if row.pk not in matched_charge_ids]
    report.unmatched_invoices = [row for row in invoices if row.pk not in matched_invoice_ids]
    report.supplier_invoice_count = len(invoices)
    report.supplier_invoices = _sum_invoices(invoices)
    report.supplier_bank = quantise(sum((abs(row.amount) for row in charges), ZERO))
    if report.supplier_invoices is not None:
        report.supplier_gap = quantise(report.supplier_invoices - report.supplier_bank)

    shop = summarise(period_profits, label="Shop", date_range=date_range)
    if shop.lines_total:
        known = shop.supplier_cost + shop.shipping_cost + shop.supplier_tax
        report.estimated_fulfilment = quantise(known) if shop.is_complete else None
        report.fulfilment_coverage_pct = shop.completeness_pct

    dated_invoices = [row for row in all_invoices if row.placed_at]
    if dated_invoices:
        first = min(row.placed_at.date() for row in dated_invoices)
        last = max(row.placed_at.date() for row in dated_invoices)
        report.invoice_span = f"{first:%b %Y} – {last:%b %Y}"

    report.months = _monthly_series(date_range, all_days, all_payouts, all_charges)
    report.notes = _notes(report, all_invoices)
    return report


def _monthly_series(
    date_range: DateRange,
    days: list[SaleDay],
    payouts: list[BankTransaction],
    charges: list[BankTransaction],
) -> list[dict]:
    months = date_range.months()
    by_month: dict[str, dict] = {}
    for month in months:
        by_month[month.label] = {
            "label": month.label,
            "expected": ZERO,
            "payouts": ZERO,
            "supplier": ZERO,
        }
    for day in days:
        for month in months:
            if month.contains(day.sale_on):
                by_month[month.label]["expected"] += day.expected_cash
                break
    for row in payouts:
        for month in months:
            if month.contains(row.occurred_on):
                by_month[month.label]["payouts"] += row.amount
                break
    for row in charges:
        for month in months:
            if month.contains(row.occurred_on):
                by_month[month.label]["supplier"] += abs(row.amount)
                break
    out = []
    for month in months:
        item = by_month[month.label]
        item["expected"] = quantise(item["expected"])
        item["payouts"] = quantise(item["payouts"])
        item["supplier"] = quantise(item["supplier"])
        out.append(item)
    return out


def _notes(report: ReconciliationReport, all_invoices: list[SupplierOrder]) -> list[str]:
    notes: list[str] = []
    if report.cash_gap > AMOUNT_TOLERANCE:
        notes.append(
            "Shopify expected cash is higher than Stripe/Adyen deposits in this period. "
            "That is usually timing — a later payout — or an unmatched batch, not missing sales."
        )
    elif report.cash_gap < -AMOUNT_TOLERANCE:
        notes.append(
            "Bank payouts exceed Shopify expected cash in this period. "
            "The extra is usually sales from just before the period, or a batched deposit."
        )
    if report.unmatched_payouts:
        notes.append(
            f"{len(report.unmatched_payouts)} payout(s) did not match a sales day within "
            f"{SHOPIFY_LAG_MAX} days at ±£{AMOUNT_TOLERANCE}. They stay unmatched."
        )
    if report.unmatched_days:
        notes.append(
            f"{len(report.unmatched_days)} sales day(s) have not landed as a matching deposit yet."
        )
    dated = [row for row in all_invoices if row.placed_at]
    if report.unmatched_charges and dated:
        last = max(row.placed_at.date() for row in dated)
        if last < report.date_range.start:
            notes.append(
                f"Inkthreadable invoices in the database end {last:%d %b %Y}. "
                "Recent bank charges cannot be matched until newer supplier orders are synced. "
                "They are left unmatched, not guessed."
            )
    elif report.unmatched_charges and not dated:
        notes.append(
            "Inkthreadable bank charges have no supplier invoices to match against."
        )
    if report.estimated_fulfilment is None and report.fulfilment_coverage_pct is not None:
        notes.append(
            f"Mapped fulfilment cost covers {report.fulfilment_coverage_pct}% of sold lines. "
            "The rest is unknown, so estimated supplier cost is not shown as a total."
        )
    return notes


def find_live_suggestion(transaction_id: int, kind: str) -> SuggestedLink | None:
    """Recompute suggestions and return the one for this bank row, if any."""
    confirmed = _confirmed_links()
    reserved_txn = {link.bank_transaction_id for link in confirmed}
    reserved_dates = {day for link in confirmed for day in link.sale_dates}
    reserved_invoices = {link.supplier_order_id for link in confirmed if link.supplier_order_id}

    pool = (
        match_shopify_days(
            sale_days(),
            payout_transactions(),
            reserved_txn_ids=reserved_txn,
            reserved_dates=reserved_dates,
        )
        if kind == CashLinkKind.SHOPIFY_DAY
        else match_supplier_charges(
            supplier_charges(),
            list(SupplierOrder.objects.all()),
            reserved_txn_ids=reserved_txn,
            reserved_invoice_ids=reserved_invoices,
        )
    )
    return next((row for row in pool if row.bank_transaction.pk == transaction_id), None)
