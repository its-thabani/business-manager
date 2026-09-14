"""Importer for the existing Finance Dashboard workbook.

The workbook holds the historical source of truth and is read strictly
read-only: nothing is written back to it, and the file is never modified.

Two sheets contain transaction records, in different shapes and from different
bank accounts:

``Transactions`` — the Monzo era, May 2025 onward.
    A: Date Time   B: Transaction Name   C: Amount   D: Transaction ID   E: Category

``Historic Data`` — the previous bank account, January 2023 to May 2025.
    A: Date   B: Transaction Name   C: Amount   D: Category

They are imported into separate ``BankAccount`` records because the May 2025
account migration appears in both: as money leaving the old account and as money
arriving in Monzo. Keeping the accounts apart is what allows those two halves to
be identified as one internal transfer rather than counted as income.

The category recorded in the spreadsheet is imported as-is and marked
``IMPORTED``, so the new system starts out agreeing with the old one exactly.
Rules can then be re-run to refine classifications, and any disagreement is
reported rather than applied silently.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl
from django.db import transaction as db_transaction

from apps.core.money import to_decimal
from apps.finance.importers.base import ImportReport, RowIssue, file_checksum
from apps.finance.models import (
    BankAccount,
    BankTransaction,
    Category,
    CategorySource,
    ImportBatch,
    ImportIssue,
    IssueCode,
    IssueSeverity,
    TransactionSource,
)

logger = logging.getLogger(__name__)

TRANSACTIONS_SHEET = "Transactions"
HISTORIC_SHEET = "Historic Data"


def _coerce_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _category_lookup() -> dict[str, Category]:
    """Map spreadsheet category labels onto ``Category`` rows.

    Both the legacy label and the current name are accepted, and matching is
    case-insensitive, because the sheets contain minor inconsistencies such as
    "Web Hosting" alongside "Store Hosting".
    """
    lookup: dict[str, Category] = {}
    for category in Category.objects.all():
        lookup[category.name.casefold()] = category
        if category.legacy_name:
            lookup[category.legacy_name.casefold()] = category
    # Label variants observed in the workbook.
    if "store hosting" in lookup:
        lookup.setdefault("web hosting", lookup["store hosting"])
    return lookup


@db_transaction.atomic
def import_finance_dashboard(
    path: str | Path,
    *,
    monzo_account: BankAccount | None = None,
    legacy_account: BankAccount | None = None,
    dry_run: bool = False,
) -> list[ImportReport]:
    """Import both transaction sheets from the Finance Dashboard workbook.

    Returns one report per sheet. Idempotent: re-running imports nothing new.
    """
    path = Path(path)
    checksum = file_checksum(path)

    if monzo_account is None:
        monzo_account, _ = BankAccount.objects.get_or_create(
            name="Monzo Business",
            defaults={"institution": "Monzo", "currency": "GBP", "is_primary": True},
        )
    if legacy_account is None:
        legacy_account, _ = BankAccount.objects.get_or_create(
            name="Legacy Business Account",
            defaults={
                "institution": "Previous bank (pre-Monzo)",
                "currency": "GBP",
                "notes": (
                    "Historical records imported from the Finance Dashboard 'Historic Data' sheet, "
                    "covering January 2023 to the May 2025 migration to Monzo."
                ),
            },
        )

    # read_only speeds up the load and data_only takes the cached formula results
    # rather than the formulas themselves.
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    categories = _category_lookup()
    reports: list[ImportReport] = []

    try:
        if TRANSACTIONS_SHEET in workbook.sheetnames:
            reports.append(
                _import_sheet(
                    workbook[TRANSACTIONS_SHEET],
                    account=monzo_account,
                    source=TransactionSource.LEGACY_TRANSACTIONS,
                    columns={"date": 1, "name": 2, "amount": 3, "external_id": 4, "category": 5},
                    first_data_row=2,
                    filename=f"{path.name} :: {TRANSACTIONS_SHEET}",
                    checksum=checksum,
                    categories=categories,
                )
            )
        if HISTORIC_SHEET in workbook.sheetnames:
            reports.append(
                _import_sheet(
                    workbook[HISTORIC_SHEET],
                    account=legacy_account,
                    source=TransactionSource.LEGACY_HISTORIC,
                    columns={"date": 1, "name": 2, "amount": 3, "external_id": None, "category": 4},
                    first_data_row=2,
                    filename=f"{path.name} :: {HISTORIC_SHEET}",
                    checksum=checksum,
                    categories=categories,
                )
            )
    finally:
        workbook.close()

    if dry_run:
        db_transaction.set_rollback(True)

    return reports


def _import_sheet(
    sheet,
    *,
    account: BankAccount,
    source: str,
    columns: dict[str, int | None],
    first_data_row: int,
    filename: str,
    checksum: str,
    categories: dict[str, Category],
) -> ImportReport:
    report = ImportReport(source=source, filename=filename)

    batch = ImportBatch.objects.create(
        source=source,
        account=account,
        filename=filename,
        file_checksum=checksum,
    )
    report.batch_id = batch.pk

    existing = set(BankTransaction.objects.filter(account=account).values_list("fingerprint", flat=True))
    seen: set[str] = set()
    new_rows: list[BankTransaction] = []
    unknown_labels: dict[str, int] = {}
    # Counts rows sharing the same date, amount and narrative, so that genuine
    # repeat payments are kept apart. See BankTransaction.build_fingerprint.
    occurrences: Counter = Counter()
    true_duplicates: list[int] = []
    issue_rows: list[ImportIssue] = []

    date_col = columns["date"]
    name_col = columns["name"]
    amount_col = columns["amount"]
    id_col = columns["external_id"]
    category_col = columns["category"]

    for row_number, row in enumerate(sheet.iter_rows(min_row=first_data_row, values_only=True), start=first_data_row):
        def cell(index: int | None):
            if index is None or index > len(row):
                return None
            return row[index - 1]

        raw_date = cell(date_col)
        raw_amount = cell(amount_col)

        # Trailing blank rows are expected: the sheet's ranges extend well past
        # the real data.
        if raw_date is None and raw_amount is None:
            continue

        report.rows_read += 1

        occurred_on = _coerce_date(raw_date)
        if occurred_on is None:
            report.skipped += 1
            report.issues.append(RowIssue(row_number, f"unreadable date {raw_date!r}", {"row": list(row)}))
            issue_rows.append(
                ImportIssue(
                    batch=batch,
                    row_number=row_number,
                    code=IssueCode.UNREADABLE_DATE,
                    severity=IssueSeverity.ERROR,
                    message=f"Could not read a date from {raw_date!r}; row not imported.",
                    raw={"sheet": sheet.title, "row": row_number},
                )
            )
            continue

        amount = to_decimal(raw_amount)
        if amount is None:
            report.skipped += 1
            report.issues.append(RowIssue(row_number, f"unreadable amount {raw_amount!r}", {"row": list(row)}))
            issue_rows.append(
                ImportIssue(
                    batch=batch,
                    row_number=row_number,
                    code=IssueCode.UNREADABLE_AMOUNT,
                    severity=IssueSeverity.ERROR,
                    message=f"Could not read an amount from {raw_amount!r}; row not imported.",
                    occurred_on=occurred_on,
                    raw={"sheet": sheet.title, "row": row_number},
                )
            )
            continue

        name = str(cell(name_col) or "").strip()
        external_id = str(cell(id_col) or "").strip() if id_col else ""
        label = str(cell(category_col) or "").strip()

        category = categories.get(label.casefold()) if label else None
        if label and category is None:
            unknown_labels[label] = unknown_labels.get(label, 0) + 1
            report.issues.append(
                RowIssue(row_number, f"unrecognised category label {label!r}; left uncategorised", {"row": list(row)})
            )
            issue_rows.append(
                ImportIssue(
                    batch=batch,
                    row_number=row_number,
                    code=IssueCode.UNKNOWN_CATEGORY,
                    severity=IssueSeverity.WARNING,
                    message=f"Category label {label!r} is not recognised; the row was imported uncategorised.",
                    occurred_on=occurred_on,
                    amount=amount,
                    raw={"sheet": sheet.title, "row": row_number, "label": label},
                )
            )

        content_key = (occurred_on, amount, " ".join(name.split()).casefold())
        occurrences[content_key] += 1

        fingerprint = BankTransaction.build_fingerprint(
            account_id=account.pk,
            external_id=external_id,
            occurred_on=occurred_on,
            amount=amount,
            counterparty=name,
            description="",
            occurrence=occurrences[content_key],
        )
        if fingerprint in existing or fingerprint in seen:
            report.duplicates += 1
            # Only a repeat *within this file* indicates a data problem. A row
            # already in the database simply means the file has been imported
            # before, which is normal and must not raise an issue.
            if external_id and fingerprint in seen:
                true_duplicates.append(row_number)
                message = (
                    f"Transaction ID {external_id} already imported. This row duplicates another in the "
                    f"sheet, so {amount:+.2f} was counted twice in the old totals and once here."
                )
                report.issues.append(RowIssue(row_number, message, {"row": list(row)}))
                issue_rows.append(
                    ImportIssue(
                        batch=batch,
                        row_number=row_number,
                        code=IssueCode.DUPLICATE_EXTERNAL_ID,
                        severity=IssueSeverity.WARNING,
                        message=message,
                        occurred_on=occurred_on,
                        amount=amount,
                        raw={"sheet": sheet.title, "row": row_number, "external_id": external_id},
                    )
                )
            continue
        seen.add(fingerprint)

        new_rows.append(
            BankTransaction(
                account=account,
                source=source,
                import_batch=batch,
                external_id=external_id,
                source_row_number=row_number,
                fingerprint=fingerprint,
                occurred_on=occurred_on,
                counterparty=name,
                description=name,
                amount=amount,
                currency="GBP",
                category=category,
                category_source=CategorySource.IMPORTED if category else CategorySource.UNCATEGORISED,
                # Spreadsheet labels are the starting point. Monzo import must
                # not overwrite them; Apply rules can, so a new HMRC → Tax
                # rule replaces a generic Expenditure label.
                is_category_locked=bool(category),
                raw={"sheet": sheet.title, "row": row_number, "values": [_json_safe(v) for v in row]},
            )
        )
        report.note_amount(amount)
        report.note_date(occurred_on)

    BankTransaction.objects.bulk_create(new_rows, batch_size=500)
    ImportIssue.objects.bulk_create(issue_rows, batch_size=500)
    report.created = len(new_rows)

    batch.rows_read = report.rows_read
    batch.rows_created = report.created
    batch.rows_duplicate = report.duplicates
    batch.rows_skipped = report.skipped
    notes = []
    if unknown_labels:
        notes.append(
            "Unrecognised category labels: "
            + ", ".join(f"{label} ({count})" for label, count in sorted(unknown_labels.items()))
        )
    if true_duplicates:
        notes.append(
            f"Rows repeating an already-imported bank transaction ID (duplicated in the sheet): "
            f"{', '.join(str(r) for r in true_duplicates)}"
        )
    batch.notes = " | ".join(notes)
    batch.save()

    logger.info(
        "Imported %s: read=%s created=%s duplicates=%s skipped=%s",
        filename,
        report.rows_read,
        report.created,
        report.duplicates,
        report.skipped,
    )
    return report


def _json_safe(value):
    """Make a cell value storable in a JSONField without losing information."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
