"""Bank transactions, categories and categorisation rules.

This is the replacement for the ``Transactions``, ``Historic Data`` and
``Categorisation`` sheets of the Finance Dashboard spreadsheet.

Two principles drive the design:

1. **Raw data is never destroyed.** Every imported row keeps its original
   payload in ``BankTransaction.raw`` and its untouched bank narrative. Anything
   derived (category, classification) is stored separately so it can be
   recomputed without needing to re-import.

2. **Classification is data, not code.** Whether a transaction counts as
   revenue, a cost of goods sold, or should be ignored entirely is determined by
   its ``Category``, which is editable through the UI. The spreadsheet inferred
   this from the sign of the amount, which silently miscounts refunds.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from django.db import models
from django.utils import timezone

from apps.core.models import MoneyField, TimeStampedModel


class CategoryKind(models.TextChoices):
    """How a category participates in the profit and loss calculation.

    The ``kind`` is what the financial engine reads; the category *name* is
    purely a human label. This means renaming "Apparel" to "Supplier costs"
    cannot break any calculation.
    """

    REVENUE = "REVENUE", "Revenue"
    COGS = "COGS", "Cost of goods sold"
    SHIPPING = "SHIPPING", "Shipping & postage"
    FEES = "FEES", "Payment & platform fees"
    OPERATING = "OPERATING", "Operating expense"
    DISTRIBUTION = "DISTRIBUTION", "Owner distribution (salary / tithe)"
    TRANSFER = "TRANSFER", "Internal transfer"
    EXCLUDED = "EXCLUDED", "Excluded from reporting"

    @classmethod
    def direct_costs(cls) -> tuple[str, ...]:
        """Costs attributable to fulfilling a sale, used for contribution profit."""
        return (cls.COGS, cls.SHIPPING, cls.FEES)

    @classmethod
    def outside_pnl(cls) -> tuple[str, ...]:
        """Kinds that represent moving money, not earning or spending it."""
        return (cls.TRANSFER, cls.EXCLUDED)


class Category(TimeStampedModel):
    """A user-editable transaction category.

    Seeded from the existing spreadsheet's category list so historical data
    imports with its original labels intact.
    """

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20, choices=CategoryKind.choices)
    description = models.CharField(max_length=255, blank=True)
    # Preserves the exact label used in the spreadsheet so legacy rows round-trip
    # even if the category is later renamed here.
    legacy_name = models.CharField(max_length=100, blank=True, db_index=True)
    colour = models.CharField(max_length=7, blank=True, help_text="Hex colour for charts, e.g. #4F46E5")
    sort_order = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    @property
    def is_direct_cost(self) -> bool:
        return self.kind in CategoryKind.direct_costs()

    @property
    def counts_toward_pnl(self) -> bool:
        return self.kind not in CategoryKind.outside_pnl()


class MatchType(models.TextChoices):
    CONTAINS = "CONTAINS", "Contains"
    NOT_CONTAINS = "NOT_CONTAINS", "Does not contain"
    STARTS_WITH = "STARTS_WITH", "Starts with"
    ENDS_WITH = "ENDS_WITH", "Ends with"
    EXACT = "EXACT", "Exactly equals"
    REGEX = "REGEX", "Matches regular expression"


class MatchField(models.TextChoices):
    """Which part of a transaction a rule inspects.

    A Monzo export carries several overlapping text fields. ``counterparty`` is
    the cleaned merchant name ("Inkthreadable"), while ``description`` is the raw
    bank narrative ("INKTHREADABLE          BLACKBURN     GBR"). Rules default to
    ``ANY_TEXT`` so they work across both, and across the older spreadsheet rows
    whose narrative format differs.
    """

    COUNTERPARTY = "COUNTERPARTY", "Counterparty name"
    DESCRIPTION = "DESCRIPTION", "Bank description"
    NOTES = "NOTES", "Notes / reference"
    ANY_TEXT = "ANY_TEXT", "Any text field"


class AmountCondition(models.TextChoices):
    """Optional sign restriction on a rule.

    Needed because a single counterparty can produce both costs and credits.
    Inkthreadable normally debits the account, but issues occasional refunds as
    credits; those should reduce cost of goods sold, not be booked as revenue.
    """

    ANY = "ANY", "Any amount"
    NEGATIVE = "NEGATIVE", "Money out only"
    POSITIVE = "POSITIVE", "Money in only"


class CategoryRule(TimeStampedModel):
    """A configurable rule that assigns a category to matching transactions.

    Rules are evaluated in ``priority`` order (lowest first) and the first match
    wins, so a specific rule can be placed ahead of a broader one.
    """

    name = models.CharField(max_length=120, blank=True, help_text="Optional label to describe this rule")
    pattern = models.CharField(max_length=255, help_text="Text to match against the transaction")
    match_type = models.CharField(max_length=20, choices=MatchType.choices, default=MatchType.CONTAINS)
    match_field = models.CharField(max_length=20, choices=MatchField.choices, default=MatchField.ANY_TEXT)
    amount_condition = models.CharField(
        max_length=10, choices=AmountCondition.choices, default=AmountCondition.ANY
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="rules")
    priority = models.IntegerField(
        default=100, help_text="Lower numbers are evaluated first. The first matching rule wins."
    )
    is_active = models.BooleanField(default=True)
    # Populated by the categorisation engine so the UI can show which rules are
    # doing real work and which are dead weight.
    match_count = models.IntegerField(default=0, editable=False)
    last_matched_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["priority", "pk"]

    def __str__(self) -> str:
        return self.name or f"{self.get_match_type_display()} '{self.pattern}' → {self.category.name}"

    def matches(self, transaction: BankTransaction) -> bool:
        """Whether this rule applies to ``transaction``."""
        if not self.is_active:
            return False

        if self.amount_condition == AmountCondition.NEGATIVE and transaction.amount >= 0:
            return False
        if self.amount_condition == AmountCondition.POSITIVE and transaction.amount <= 0:
            return False

        return any(self._text_matches(text) for text in self._candidate_texts(transaction))

    def _candidate_texts(self, transaction: BankTransaction) -> list[str]:
        if self.match_field == MatchField.COUNTERPARTY:
            return [transaction.counterparty]
        if self.match_field == MatchField.DESCRIPTION:
            return [transaction.description]
        if self.match_field == MatchField.NOTES:
            return [transaction.notes]
        return [transaction.counterparty, transaction.description, transaction.notes]

    def _text_matches(self, text: str) -> bool:
        if not text:
            return False
        # Bank narratives pad fields with runs of spaces and tabs; collapse them
        # so a pattern does not have to reproduce the exact whitespace.
        haystack = re.sub(r"\s+", " ", text).strip().casefold()
        needle = re.sub(r"\s+", " ", self.pattern).strip().casefold()
        if not needle:
            return False

        if self.match_type == MatchType.CONTAINS:
            return needle in haystack
        if self.match_type == MatchType.NOT_CONTAINS:
            return needle not in haystack
        if self.match_type == MatchType.STARTS_WITH:
            return haystack.startswith(needle)
        if self.match_type == MatchType.ENDS_WITH:
            return haystack.endswith(needle)
        if self.match_type == MatchType.EXACT:
            return haystack == needle
        if self.match_type == MatchType.REGEX:
            try:
                return re.search(self.pattern, text, re.IGNORECASE) is not None
            except re.error:
                # An invalid pattern must not break an entire import run.
                return False
        return False


class TransactionSource(models.TextChoices):
    MONZO_CSV = "MONZO_CSV", "Monzo CSV export"
    LEGACY_TRANSACTIONS = "LEGACY_TRANSACTIONS", "Spreadsheet — Transactions sheet"
    LEGACY_HISTORIC = "LEGACY_HISTORIC", "Spreadsheet — Historic Data sheet"
    MANUAL = "MANUAL", "Entered manually"


class CategorySource(models.TextChoices):
    """Provenance of a transaction's category, so manual work is never lost."""

    UNCATEGORISED = "UNCATEGORISED", "Uncategorised"
    RULE = "RULE", "Matched a rule"
    MANUAL = "MANUAL", "Set manually"
    IMPORTED = "IMPORTED", "Imported from spreadsheet"


class BankAccount(TimeStampedModel):
    """A bank account whose transactions are tracked.

    Modelled explicitly because the business has moved between banks: the
    ``Historic Data`` rows came from a previous account (Adyen/PayPal era) and
    current rows come from Monzo. Keeping them apart makes the migration
    transfers identifiable instead of appearing as revenue.
    """

    name = models.CharField(max_length=100, unique=True)
    institution = models.CharField(max_length=100, blank=True)
    currency = models.CharField(max_length=3, default="GBP")
    is_primary = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-is_primary", "name"]

    def __str__(self) -> str:
        return self.name


class ImportBatch(TimeStampedModel):
    """An audit record of one import run.

    Retained permanently so any figure can be traced back to the file it came
    from, and so the Data Health page can report when each source was last
    refreshed.
    """

    source = models.CharField(max_length=30, choices=TransactionSource.choices)
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="import_batches")
    filename = models.CharField(max_length=255, blank=True)
    # Lets a re-upload of a byte-identical file be recognised immediately.
    file_checksum = models.CharField(max_length=64, blank=True, db_index=True)
    rows_read = models.IntegerField(default=0)
    rows_created = models.IntegerField(default=0)
    rows_duplicate = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "import batches"

    def __str__(self) -> str:
        return f"{self.get_source_display()} — {self.filename or self.created_at:%Y-%m-%d %H:%M}"


class IssueSeverity(models.TextChoices):
    INFO = "INFO", "Information"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"


class IssueCode(models.TextChoices):
    """Machine-readable reasons a source row was not imported cleanly."""

    DUPLICATE_EXTERNAL_ID = "DUPLICATE_EXTERNAL_ID", "Row repeats an already-imported bank transaction ID"
    UNREADABLE_DATE = "UNREADABLE_DATE", "Date could not be read"
    UNREADABLE_AMOUNT = "UNREADABLE_AMOUNT", "Amount could not be read"
    UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY", "Category label not recognised"


class ImportIssue(TimeStampedModel):
    """A source row that needed attention during an import.

    Kept permanently and surfaced on the Data Health page, so it is always clear
    when the figures rest on incomplete or contradictory source data. ``amount``
    is recorded where known, so an issue's financial impact can be quantified
    rather than merely noted.
    """

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="issues")
    row_number = models.IntegerField(null=True, blank=True)
    code = models.CharField(max_length=40, choices=IssueCode.choices)
    severity = models.CharField(max_length=10, choices=IssueSeverity.choices, default=IssueSeverity.WARNING)
    message = models.TextField()
    occurred_on = models.DateField(null=True, blank=True, db_index=True)
    amount = MoneyField(null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    is_acknowledged = models.BooleanField(
        default=False, help_text="Tick once you have reviewed this and decided no action is needed"
    )

    class Meta:
        ordering = ["-created_at", "row_number"]

    def __str__(self) -> str:
        return f"{self.get_code_display()} (row {self.row_number})"


class BankTransactionQuerySet(models.QuerySet):
    def in_range(self, date_range) -> BankTransactionQuerySet:
        return self.filter(occurred_on__gte=date_range.start, occurred_on__lte=date_range.end)

    def reportable(self) -> BankTransactionQuerySet:
        """Exclude internal transfers and explicitly excluded rows."""
        return self.exclude(category__kind__in=CategoryKind.outside_pnl())

    def uncategorised(self) -> BankTransactionQuerySet:
        return self.filter(category__isnull=True)

    def of_kind(self, *kinds: str) -> BankTransactionQuerySet:
        return self.filter(category__kind__in=kinds)


class BankTransaction(TimeStampedModel):
    """A single line of a bank statement.

    One row per real movement of money. The ``fingerprint`` guarantees that
    re-importing an overlapping CSV export cannot create duplicates.
    """

    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="transactions")
    source = models.CharField(max_length=30, choices=TransactionSource.choices)
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )

    # --- Identity -----------------------------------------------------------
    # The bank's own identifier where one exists. Monzo has used both "tx_" and
    # "mm_" prefixes over time, and the legacy spreadsheet rows have none at all,
    # so this cannot be the only deduplication key.
    external_id = models.CharField(max_length=100, blank=True, db_index=True)
    # Row this came from in the source spreadsheet, where applicable. Kept so a
    # figure can be traced back to a specific line of the original workbook.
    source_row_number = models.IntegerField(null=True, blank=True)
    fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        editable=False,
        help_text="Content hash used to detect duplicates across overlapping imports",
    )

    # --- When ---------------------------------------------------------------
    occurred_on = models.DateField(db_index=True)
    # Kept separately because the legacy sheets only recorded a date.
    occurred_at = models.DateTimeField(null=True, blank=True)

    # --- What (preserved exactly as supplied) -------------------------------
    counterparty = models.CharField(max_length=255, blank=True, help_text="Merchant or payee name")
    description = models.TextField(blank=True, help_text="Raw bank narrative, unmodified")
    notes = models.CharField(max_length=500, blank=True, help_text="Reference or notes from the bank export")
    transaction_type = models.CharField(max_length=60, blank=True, help_text="e.g. Card payment, Faster payment")
    bank_category = models.CharField(
        max_length=60, blank=True, help_text="The bank's own guess at a category. Informational only."
    )

    # --- How much -----------------------------------------------------------
    amount = MoneyField(help_text="Signed: positive is money in, negative is money out")
    currency = models.CharField(max_length=3, default="GBP")
    local_amount = MoneyField(null=True, blank=True, help_text="Original amount if the charge was not in GBP")
    local_currency = models.CharField(max_length=3, blank=True)
    balance_after = MoneyField(null=True, blank=True)

    # --- Classification -----------------------------------------------------
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, null=True, blank=True, related_name="transactions"
    )
    category_source = models.CharField(
        max_length=20, choices=CategorySource.choices, default=CategorySource.UNCATEGORISED
    )
    matched_rule = models.ForeignKey(
        CategoryRule, on_delete=models.SET_NULL, null=True, blank=True, related_name="matched_transactions"
    )
    is_category_locked = models.BooleanField(
        default=False,
        help_text="Protects a manually chosen category from being overwritten when rules are re-run",
    )

    internal_notes = models.TextField(blank=True, help_text="Your own notes. Never touched by an import.")
    raw = models.JSONField(default=dict, blank=True, help_text="The complete original source row")

    objects = BankTransactionQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_on", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "fingerprint"], name="unique_transaction_per_account"
            ),
        ]
        indexes = [
            models.Index(fields=["occurred_on", "category"]),
            models.Index(fields=["source"]),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_on:%d %b %Y} {self.counterparty or self.description[:40]} {self.amount:+.2f}"

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def build_fingerprint(
        *,
        account_id: int,
        external_id: str,
        occurred_on,
        amount,
        counterparty: str,
        description: str,
        occurrence: int = 1,
    ) -> str:
        """Produce the content hash used to detect duplicate imports.

        When the bank supplies an identifier it is authoritative and is used
        alone, so an unchanged row re-exported with tidied-up text is still
        recognised, and a genuinely repeated identifier is rejected.

        Without an identifier — as in the legacy spreadsheet's ``Historic Data``
        sheet — the hash falls back to the date, amount and narrative. That
        combination is not unique in practice: two Inkthreadable orders of the
        same value on the same day are ordinary for a print-on-demand business
        and are two real payments, not one duplicated row. ``occurrence`` is the
        1-based index of the row within its identical group, which keeps those
        genuine repeats distinct while still making a re-import idempotent,
        because the same file always yields the same indices.
        """
        if external_id:
            payload = f"{account_id}|id|{external_id}"
        else:
            narrative = re.sub(r"\s+", " ", f"{counterparty} {description}").strip().casefold()
            payload = (
                f"{account_id}|content|{occurred_on:%Y-%m-%d}|{amount:.2f}|{narrative}|occ{occurrence}"
            )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if not self.fingerprint:
            self.fingerprint = self.build_fingerprint(
                account_id=self.account_id,
                external_id=self.external_id,
                occurred_on=self.occurred_on,
                amount=self.amount,
                counterparty=self.counterparty,
                description=self.description,
            )
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    @property
    def is_money_in(self) -> bool:
        return self.amount > 0

    @property
    def display_name(self) -> str:
        return self.counterparty or re.sub(r"\s+", " ", self.description).strip() or "(no description)"

    def apply_category(self, category: Category | None, source: str, rule: CategoryRule | None = None) -> bool:
        """Assign a category, returning whether anything actually changed."""
        if self.category_id == (category.pk if category else None) and self.category_source == source:
            return False
        self.category = category
        self.category_source = source
        self.matched_rule = rule
        return True

    def set_manual_category(self, category: Category | None, *, lock: bool = True) -> None:
        """Record a human decision about this transaction's category."""
        self.category = category
        self.category_source = CategorySource.MANUAL if category else CategorySource.UNCATEGORISED
        self.matched_rule = None
        self.is_category_locked = lock
        self.save(update_fields=["category", "category_source", "matched_rule", "is_category_locked", "updated_at"])


class CashLinkKind(models.TextChoices):
    SHOPIFY_DAY = "shopify_day", "Shopify sales day"
    SUPPLIER_ORDER = "supplier_order", "Supplier invoice"
    MANUAL = "manual", "Linked by hand"


class CashLinkConfidence(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed by me"
    SUGGESTED = "suggested", "Suggested automatically"


class CashLink(TimeStampedModel):
    """A bank row linked to the Shopify day or supplier invoice it belongs to.

    Confirmed links are never overwritten by the matcher. Suggested links can be
    replaced when the algorithm finds a better fit, the same way product
    mappings work.
    """

    bank_transaction = models.OneToOneField(
        BankTransaction,
        on_delete=models.CASCADE,
        related_name="cash_link",
    )
    kind = models.CharField(max_length=20, choices=CashLinkKind.choices)
    confidence = models.CharField(
        max_length=20,
        choices=CashLinkConfidence.choices,
        default=CashLinkConfidence.SUGGESTED,
        db_index=True,
    )
    sale_on = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="The Shopify sales day this payout covers (first day if several).",
    )
    extra_sale_dates = models.JSONField(
        default=list,
        blank=True,
        help_text="Further sales days when one payout covers a run of days.",
    )
    supplier_order = models.ForeignKey(
        "supplier.SupplierOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_links",
    )
    expected_amount = MoneyField(
        null=True,
        blank=True,
        help_text="What the matcher thought would land, so a later drift is visible.",
    )
    match_reason = models.CharField(max_length=300, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-bank_transaction__occurred_on", "-pk"]

    def __str__(self) -> str:
        return f"{self.bank_transaction} → {self.get_kind_display()}"

    @property
    def is_confirmed(self) -> bool:
        return self.confidence == CashLinkConfidence.CONFIRMED

    @property
    def sale_dates(self) -> list:
        """Every sales day this link covers, including the primary day."""
        dates = []
        if self.sale_on is not None:
            dates.append(self.sale_on)
        for raw in self.extra_sale_dates or []:
            if isinstance(raw, str):
                parsed = date.fromisoformat(raw)
                if parsed not in dates:
                    dates.append(parsed)
        return dates


class CategorisationRun(TimeStampedModel):
    """Audit record of a rules re-run, for the Data Health page."""

    transactions_examined = models.IntegerField(default=0)
    transactions_changed = models.IntegerField(default=0)
    transactions_locked_skipped = models.IntegerField(default=0)
    left_uncategorised = models.IntegerField(default=0)
    finished_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Categorisation {self.created_at:%Y-%m-%d %H:%M} — {self.transactions_changed} changed"
