"""The transaction categorisation engine.

Rules are stored in the database and evaluated in priority order, first match
wins. Re-running is always safe: a category set by hand on Expenses is locked
and is never overwritten. Spreadsheet labels are starting points — Apply rules
can replace them when a more specific rule matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction as db_transaction
from django.utils import timezone

from apps.finance.models import (
    BankTransaction,
    CategorisationRun,
    Category,
    CategoryRule,
    CategorySource,
)

logger = logging.getLogger(__name__)


@dataclass
class CategorisationResult:
    examined: int = 0
    changed: int = 0
    locked_skipped: int = 0
    uncategorised: int = 0
    rule_hits: dict[int, int] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return (
            f"examined={self.examined} changed={self.changed} "
            f"locked_skipped={self.locked_skipped} still_uncategorised={self.uncategorised}"
        )


def find_matching_rule(txn: BankTransaction, rules: list[CategoryRule]) -> CategoryRule | None:
    """Return the first rule in ``rules`` that matches, or ``None``.

    ``rules`` must already be ordered by priority; it is passed in rather than
    queried per transaction so a full re-run is a single database read.
    """
    for rule in rules:
        if rule.matches(txn):
            return rule
    return None


def suggest_category(txn: BankTransaction) -> tuple[Category | None, CategoryRule | None]:
    """Categorise a single transaction without saving. Used for import previews."""
    rules = list(CategoryRule.objects.filter(is_active=True).select_related("category"))
    rule = find_matching_rule(txn, rules)
    return (rule.category if rule else None), rule


@db_transaction.atomic
def categorise(
    queryset=None,
    *,
    respect_locks: bool = True,
    only_uncategorised: bool = False,
    record_run: bool = True,
) -> CategorisationResult:
    """Apply the active rules to transactions.

    Args:
        queryset: Transactions to process. Defaults to all of them.
        respect_locks: Leave rows set by hand on Expenses untouched. Spreadsheet
            (imported) categories are updated when you click Apply rules, so a
            new HMRC → Tax rule can replace a generic Expenditure label.
        only_uncategorised: Only fill in gaps, never revisit existing categories.
            Useful after adding a rule for a previously unrecognised payee.
        record_run: Write a ``CategorisationRun`` audit row.
    """
    if queryset is None:
        queryset = BankTransaction.objects.all()
    if only_uncategorised:
        queryset = queryset.filter(category__isnull=True)

    rules = list(CategoryRule.objects.filter(is_active=True).select_related("category").order_by("priority", "pk"))
    if not rules:
        logger.warning("No active categorisation rules; nothing to apply.")

    result = CategorisationResult()
    to_update: list[BankTransaction] = []
    matched_rules: dict[int, CategoryRule] = {}

    for txn in queryset.select_related("category", "matched_rule").iterator(chunk_size=500):
        result.examined += 1

        if respect_locks and txn.is_category_locked and txn.category_source == CategorySource.MANUAL:
            result.locked_skipped += 1
            if txn.category_id is None:
                result.uncategorised += 1
            continue

        rule = find_matching_rule(txn, rules)
        if rule is None:
            # A transaction that previously matched a rule but no longer does is
            # cleared, so a rule change is fully reflected rather than leaving a
            # stale category behind. Imported and manual categories are kept.
            if txn.category_source == CategorySource.RULE and txn.category_id is not None:
                txn.apply_category(None, CategorySource.UNCATEGORISED, None)
                to_update.append(txn)
                result.changed += 1
            if txn.category_id is None:
                result.uncategorised += 1
            continue

        result.rule_hits[rule.pk] = result.rule_hits.get(rule.pk, 0) + 1
        matched_rules[rule.pk] = rule

        if txn.apply_category(rule.category, CategorySource.RULE, rule):
            txn.is_category_locked = False
            to_update.append(txn)
            result.changed += 1

        if len(to_update) >= 500:
            _flush(to_update)
            to_update.clear()

    _flush(to_update)

    # Record rule usage so unused rules are visible in the UI.
    now = timezone.now()
    for rule_pk, hits in result.rule_hits.items():
        rule = matched_rules[rule_pk]
        rule.match_count = hits
        rule.last_matched_at = now
    if matched_rules:
        CategoryRule.objects.bulk_update(matched_rules.values(), ["match_count", "last_matched_at"])
    CategoryRule.objects.exclude(pk__in=result.rule_hits).update(match_count=0)

    if record_run:
        CategorisationRun.objects.create(
            transactions_examined=result.examined,
            transactions_changed=result.changed,
            transactions_locked_skipped=result.locked_skipped,
            left_uncategorised=result.uncategorised,
        )

    logger.info("Categorisation complete: %s", result.summary)
    return result


def _flush(batch: list[BankTransaction]) -> None:
    if batch:
        BankTransaction.objects.bulk_update(
            batch, ["category", "category_source", "matched_rule", "is_category_locked", "updated_at"]
        )
