"""Admin screens for reviewing and correcting finance data.

This is the working interface until the purpose-built UI is in place. It is
deliberately geared towards the two jobs that actually need doing: categorising
what the rules could not, and checking that the imports are healthy.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils.html import format_html

from apps.core.money import fmt
from apps.finance.categorisation import categorise
from apps.finance.models import (
    BankAccount,
    BankTransaction,
    CashLink,
    CategorisationRun,
    Category,
    CategoryRule,
    CategorySource,
    ImportBatch,
    ImportIssue,
)


class UncategorisedFilter(admin.SimpleListFilter):
    """The filter that matters most day to day."""

    title = "categorisation status"
    parameter_name = "cat_status"

    def lookups(self, request, model_admin):
        return [
            ("missing", "Needs a category"),
            ("manual", "Set by hand"),
            ("rule", "Set by a rule"),
            ("imported", "From the spreadsheet"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "missing":
            return queryset.filter(category__isnull=True)
        if self.value() == "manual":
            return queryset.filter(category_source=CategorySource.MANUAL)
        if self.value() == "rule":
            return queryset.filter(category_source=CategorySource.RULE)
        if self.value() == "imported":
            return queryset.filter(category_source=CategorySource.IMPORTED)
        return queryset


class DirectionFilter(admin.SimpleListFilter):
    title = "direction"
    parameter_name = "direction"

    def lookups(self, request, model_admin):
        return [("in", "Money in"), ("out", "Money out")]

    def queryset(self, request, queryset):
        if self.value() == "in":
            return queryset.filter(amount__gt=0)
        if self.value() == "out":
            return queryset.filter(amount__lt=0)
        return queryset


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_on",
        "display_name",
        "amount_display",
        "category",
        "kind_display",
        "category_source",
        "is_category_locked",
        "account",
    )
    list_filter = (
        UncategorisedFilter,
        DirectionFilter,
        "category",
        "category__kind",
        "account",
        "source",
        "is_category_locked",
        "occurred_on",
    )
    search_fields = ("counterparty", "description", "notes", "external_id", "internal_notes")
    date_hierarchy = "occurred_on"
    list_select_related = ("category", "account")
    list_per_page = 50
    # Only the classification and your own notes are editable. Everything else
    # came from the bank and must stay as it was supplied.
    readonly_fields = (
        "account",
        "source",
        "import_batch",
        "external_id",
        "source_row_number",
        "fingerprint",
        "occurred_on",
        "occurred_at",
        "counterparty",
        "description",
        "notes",
        "transaction_type",
        "bank_category",
        "amount",
        "currency",
        "local_amount",
        "local_currency",
        "balance_after",
        "matched_rule",
        "raw",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Classification", {
            "fields": ("category", "category_source", "is_category_locked", "matched_rule", "internal_notes"),
            "description": (
                "Changing the category here marks it as set by hand. Tick "
                "&ldquo;category locked&rdquo; to stop the rules from overwriting it."
            ),
        }),
        ("Transaction", {
            "fields": (
                "occurred_on", "occurred_at", "counterparty", "description", "notes",
                "transaction_type", "amount", "currency", "local_amount", "local_currency",
                "balance_after",
            ),
        }),
        ("Source", {
            "classes": ("collapse",),
            "fields": (
                "account", "source", "import_batch", "external_id", "source_row_number",
                "bank_category", "fingerprint", "raw", "created_at", "updated_at",
            ),
        }),
    )
    actions = ("action_rerun_rules", "action_lock", "action_unlock")

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj: BankTransaction) -> str:
        colour = "#15803d" if obj.amount > 0 else "#b91c1c"
        return format_html('<span style="color:{};font-variant-numeric:tabular-nums">{}</span>', colour, fmt(obj.amount))

    @admin.display(description="Treatment")
    def kind_display(self, obj: BankTransaction) -> str:
        return obj.category.get_kind_display() if obj.category else "—"

    def save_model(self, request, obj, form, change):
        # A category edited here is a human decision, so record it as such.
        if change and "category" in form.changed_data:
            obj.category_source = CategorySource.MANUAL if obj.category else CategorySource.UNCATEGORISED
            obj.matched_rule = None
            obj.is_category_locked = True
        super().save_model(request, obj, form, change)

    @admin.action(description="Re-run categorisation rules (skips locked)")
    def action_rerun_rules(self, request, queryset):
        result = categorise(queryset, record_run=False)
        self.message_user(
            request,
            f"Examined {result.examined}, changed {result.changed}, "
            f"skipped {result.locked_skipped} locked, {result.uncategorised} still need a category.",
            messages.SUCCESS,
        )

    @admin.action(description="Lock category (protect from rule changes)")
    def action_lock(self, request, queryset):
        updated = queryset.update(is_category_locked=True)
        self.message_user(request, f"Locked {updated} transaction(s).", messages.SUCCESS)

    @admin.action(description="Unlock category (allow rules to reclassify)")
    def action_unlock(self, request, queryset):
        updated = queryset.update(is_category_locked=False)
        self.message_user(request, f"Unlocked {updated} transaction(s).", messages.SUCCESS)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "swatch", "transaction_count", "total", "rule_count", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "legacy_name", "description")
    ordering = ("sort_order", "name")
    fields = ("name", "kind", "description", "legacy_name", "colour", "sort_order", "is_active")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            n=Count("transactions", distinct=True),
            total_amount=Sum("transactions__amount"),
            n_rules=Count("rules", distinct=True),
        )

    @admin.display(description="Colour")
    def swatch(self, obj) -> str:
        if not obj.colour:
            return "—"
        return format_html(
            '<span style="display:inline-block;width:2.5rem;height:1rem;'
            'border-radius:3px;background:{}"></span>',
            obj.colour,
        )

    @admin.display(description="Transactions", ordering="n")
    def transaction_count(self, obj) -> int:
        return obj.n

    @admin.display(description="Net total", ordering="total_amount")
    def total(self, obj) -> str:
        return fmt(obj.total_amount)

    @admin.display(description="Rules", ordering="n_rules")
    def rule_count(self, obj) -> int:
        return obj.n_rules


@admin.register(CategoryRule)
class CategoryRuleAdmin(admin.ModelAdmin):
    list_display = (
        "priority",
        "rule_summary",
        "category",
        "amount_condition",
        "match_count",
        "last_matched_at",
        "is_active",
    )
    list_filter = ("is_active", "match_type", "match_field", "amount_condition", "category")
    search_fields = ("pattern", "name")
    # Priority is editable inline so rules can be reordered quickly; the rule
    # summary is the link through to the full form.
    list_display_links = ("rule_summary",)
    list_editable = ("priority", "is_active")
    ordering = ("priority", "pk")
    list_select_related = ("category",)
    readonly_fields = ("match_count", "last_matched_at")
    fieldsets = (
        ("What to match", {
            "fields": ("pattern", "match_type", "match_field", "amount_condition"),
            "description": (
                "Matching ignores case and collapses runs of spaces, so a pattern does not "
                "need to reproduce the exact padding a bank uses."
            ),
        }),
        ("What to do", {"fields": ("category", "priority", "name", "is_active"),
                        "description": "Rules are tried in priority order, lowest first. The first match wins."}),
        ("Usage", {"fields": ("match_count", "last_matched_at")}),
    )
    actions = ("action_apply_rules",)

    @admin.display(description="Rule")
    def rule_summary(self, obj) -> str:
        label = f"{obj.get_match_type_display()} “{obj.pattern}”"
        if obj.name:
            return format_html("{}<br><small style='color:#666'>{}</small>", label, obj.name)
        return label

    @admin.action(description="Apply all active rules to every transaction")
    def action_apply_rules(self, request, queryset):
        result = categorise()
        self.message_user(
            request,
            f"Examined {result.examined}, changed {result.changed}, "
            f"skipped {result.locked_skipped} locked, {result.uncategorised} still need a category.",
            messages.SUCCESS,
        )


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "institution", "currency", "is_primary", "transaction_count", "date_span")

    def get_queryset(self, request):
        from django.db.models import Max, Min

        return super().get_queryset(request).annotate(
            n=Count("transactions"),
            first=Min("transactions__occurred_on"),
            last=Max("transactions__occurred_on"),
        )

    @admin.display(description="Transactions", ordering="n")
    def transaction_count(self, obj) -> int:
        return obj.n

    @admin.display(description="Covering")
    def date_span(self, obj) -> str:
        if not obj.first:
            return "—"
        return f"{obj.first:%d %b %Y} – {obj.last:%d %b %Y}"


class ImportIssueInline(admin.TabularInline):
    model = ImportIssue
    extra = 0
    can_delete = False
    fields = ("row_number", "code", "severity", "occurred_on", "amount", "message", "is_acknowledged")
    readonly_fields = ("row_number", "code", "severity", "occurred_on", "amount", "message")


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "source",
        "account",
        "filename",
        "rows_read",
        "rows_created",
        "rows_duplicate",
        "rows_skipped",
        "issue_count",
    )
    list_filter = ("source", "account")
    search_fields = ("filename", "notes")
    date_hierarchy = "created_at"
    inlines = (ImportIssueInline,)
    readonly_fields = tuple(
        f.name for f in ImportBatch._meta.fields if f.name not in {"id", "notes"}
    )

    @admin.display(description="Issues")
    def issue_count(self, obj) -> int:
        return obj.issues.count()

    def has_add_permission(self, request) -> bool:
        # Batches are created by importers, never by hand.
        return False


@admin.register(ImportIssue)
class ImportIssueAdmin(admin.ModelAdmin):
    list_display = ("created_at", "code", "severity", "occurred_on", "amount_display", "short_message", "batch_link", "is_acknowledged")
    list_filter = ("code", "severity", "is_acknowledged")
    search_fields = ("message",)
    list_editable = ("is_acknowledged",)

    @admin.display(description="Amount")
    def amount_display(self, obj) -> str:
        return fmt(obj.amount)

    @admin.display(description="Detail")
    def short_message(self, obj) -> str:
        return obj.message if len(obj.message) <= 110 else obj.message[:107] + "..."

    @admin.display(description="Import")
    def batch_link(self, obj) -> str:
        url = reverse("admin:finance_importbatch_change", args=[obj.batch_id])
        return format_html('<a href="{}">{}</a>', url, obj.batch.filename or obj.batch_id)

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(CashLink)
class CashLinkAdmin(admin.ModelAdmin):
    list_display = ("bank_transaction", "kind", "confidence", "sale_on", "supplier_order", "expected_amount")
    list_filter = ("kind", "confidence")
    search_fields = ("match_reason", "note", "bank_transaction__counterparty")
    raw_id_fields = ("bank_transaction", "supplier_order")


@admin.register(CategorisationRun)
class CategorisationRunAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "transactions_examined",
        "transactions_changed",
        "transactions_locked_skipped",
        "left_uncategorised",
    )
    readonly_fields = tuple(f.name for f in CategorisationRun._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False
