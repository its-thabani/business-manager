"""Monzo CSV export importer.

The export has the following columns (as at the September 2026 format):

    Transaction ID, Date, Time, Type, Name, Emoji, Category, Amount, Currency,
    Local amount, Local currency, Notes and #tags, Address, Receipt,
    Description, Category split, Money Out, Money In, Balance, Balance currency

Notes on the source data that this importer accounts for:

* ``Amount`` is already signed, so ``Money Out``/``Money In`` are redundant and
  are only used as a fallback if ``Amount`` is blank.
* ``Category`` is Monzo's own guess and is unreliable for this business — card
  payments to Inkthreadable arrive labelled "Sales". It is stored for reference
  but never used for classification.
* ``Date`` is ``DD/MM/YYYY``. Parsing is explicit so that, say, 03/08/2026 is
  read as 3 August and not 8 March.
* Transaction IDs have used both ``tx_`` and ``mm_`` prefixes over time.
  Deduplication keys on the full ID, so both are handled.
* Reimporting an overlapping export creates nothing new; rows already held are
  counted as duplicates and left exactly as they are, including any manual
  category.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from django.db import transaction as db_transaction
from django.utils import timezone

from apps.core.money import to_decimal
from apps.finance.importers.base import ImportReport, RowIssue, file_checksum
from apps.finance.models import (
    BankAccount,
    BankTransaction,
    ImportBatch,
    TransactionSource,
)

logger = logging.getLogger(__name__)

DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y")
TIME_FORMATS = ("%H:%M:%S", "%H:%M")

# Header variations seen across Monzo export versions, mapped to a stable key.
COLUMN_ALIASES = {
    "transaction id": "id",
    "date": "date",
    "time": "time",
    "type": "type",
    "name": "name",
    "emoji": "emoji",
    "category": "bank_category",
    "amount": "amount",
    "currency": "currency",
    "local amount": "local_amount",
    "local currency": "local_currency",
    "notes and #tags": "notes",
    "notes": "notes",
    "address": "address",
    "receipt": "receipt",
    "description": "description",
    "category split": "category_split",
    "money out": "money_out",
    "money in": "money_in",
    "balance": "balance",
    "balance currency": "balance_currency",
}


def _normalise_headers(fieldnames: list[str]) -> dict[str, str]:
    """Map the file's actual header names onto stable internal keys."""
    mapping: dict[str, str] = {}
    for name in fieldnames or []:
        key = COLUMN_ALIASES.get((name or "").strip().casefold())
        if key:
            mapping[name] = key
    return mapping


def _parse_date(value: str):
    text = (value or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(date_value, time_text: str):
    """Combine the separate date and time columns into an aware datetime."""
    if date_value is None:
        return None
    text = (time_text or "").strip()
    if not text:
        return None
    for fmt in TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        return timezone.make_aware(datetime.combine(date_value, parsed))
    return None


@db_transaction.atomic
def import_monzo_csv(
    path: str | Path,
    *,
    account: BankAccount | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Import a Monzo CSV export.

    Safe to run repeatedly on overlapping exports. Existing rows are never
    modified, so manual categorisation survives a re-import.
    """
    path = Path(path)
    report = ImportReport(source=TransactionSource.MONZO_CSV, filename=path.name)

    if account is None:
        account, _ = BankAccount.objects.get_or_create(
            name="Monzo Business",
            defaults={"institution": "Monzo", "currency": "GBP", "is_primary": True},
        )

    batch = ImportBatch.objects.create(
        source=TransactionSource.MONZO_CSV,
        account=account,
        filename=path.name,
        file_checksum=file_checksum(path),
    )
    report.batch_id = batch.pk

    # Fingerprints already held for this account, so duplicates are detected
    # without a query per row.
    existing = set(
        BankTransaction.objects.filter(account=account).values_list("fingerprint", flat=True)
    )
    seen_in_file: set[str] = set()
    new_rows: list[BankTransaction] = []
    # Only used for rows with no transaction ID; see build_fingerprint.
    occurrences: Counter = Counter()

    # utf-8-sig strips the byte-order mark Monzo prepends to its exports.
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = _normalise_headers(reader.fieldnames or [])

        missing = {"date", "amount"} - set(headers.values())
        if missing:
            raise ValueError(
                f"{path.name} does not look like a Monzo export: missing column(s) {sorted(missing)}. "
                f"Found headers: {reader.fieldnames}"
            )

        for line_number, raw_row in enumerate(reader, start=2):
            report.rows_read += 1
            row = {headers[k]: (v or "").strip() for k, v in raw_row.items() if k in headers}

            occurred_on = _parse_date(row.get("date", ""))
            if occurred_on is None:
                report.skipped += 1
                report.issues.append(
                    RowIssue(line_number, f"unreadable date {row.get('date', '')!r}", dict(raw_row))
                )
                continue

            amount = to_decimal(row.get("amount"))
            if amount is None:
                # Fall back to the split Money Out / Money In columns.
                money_out = to_decimal(row.get("money_out"), default=None)
                money_in = to_decimal(row.get("money_in"), default=None)
                amount = money_in if money_in else money_out
            if amount is None:
                report.skipped += 1
                report.issues.append(RowIssue(line_number, "no usable amount", dict(raw_row)))
                continue

            external_id = row.get("id", "")
            counterparty = row.get("name", "")
            description = row.get("description", "")

            content_key = (occurred_on, amount, " ".join(f"{counterparty} {description}".split()).casefold())
            occurrences[content_key] += 1

            fingerprint = BankTransaction.build_fingerprint(
                account_id=account.pk,
                external_id=external_id,
                occurred_on=occurred_on,
                amount=amount,
                counterparty=counterparty,
                description=description,
                occurrence=occurrences[content_key],
            )

            if fingerprint in existing or fingerprint in seen_in_file:
                report.duplicates += 1
                continue
            seen_in_file.add(fingerprint)

            new_rows.append(
                BankTransaction(
                    account=account,
                    source=TransactionSource.MONZO_CSV,
                    import_batch=batch,
                    external_id=external_id,
                    fingerprint=fingerprint,
                    occurred_on=occurred_on,
                    occurred_at=_parse_datetime(occurred_on, row.get("time", "")),
                    counterparty=counterparty,
                    description=description,
                    notes=row.get("notes", "")[:500],
                    transaction_type=row.get("type", "")[:60],
                    bank_category=row.get("bank_category", "")[:60],
                    amount=amount,
                    currency=row.get("currency") or "GBP",
                    local_amount=to_decimal(row.get("local_amount"), default=None),
                    local_currency=row.get("local_currency", "")[:3],
                    balance_after=to_decimal(row.get("balance"), default=None),
                    raw=dict(raw_row),
                )
            )
            report.note_amount(amount)
            report.note_date(occurred_on)

    if not dry_run:
        BankTransaction.objects.bulk_create(new_rows, batch_size=500)
    report.created = len(new_rows)

    batch.rows_read = report.rows_read
    batch.rows_created = report.created
    batch.rows_duplicate = report.duplicates
    batch.rows_skipped = report.skipped
    batch.notes = "Dry run — nothing written." if dry_run else ""
    batch.save()

    if dry_run:
        db_transaction.set_rollback(True)

    logger.info(
        "Monzo import %s: read=%s created=%s duplicates=%s skipped=%s",
        path.name,
        report.rows_read,
        report.created,
        report.duplicates,
        report.skipped,
    )
    return report
