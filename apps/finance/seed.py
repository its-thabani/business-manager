"""Seed data for categories and categorisation rules.

The categories and rules below are ported from the ``Categorisation`` sheet of
the existing Finance Dashboard so that historical data keeps its original
labels and the new system reproduces the old classifications.

Two things are added on top of the spreadsheet, both marked ``ADDED`` in the
rule name so they are easy to find and disable in the UI:

* Rules for counterparties that appear in the bank data but were missing from
  the sheet's rule list and so had to be categorised by hand every month
  (notably Stripe, which is how Shopify settles money into the account).
* Categories that separate genuinely different kinds of money which the
  spreadsheet lumped together under a single "Income" label. Reporting is driven
  by category *kind*, so totals are unaffected by this extra granularity.

Seeding is idempotent: running it again updates definitions in place and never
deletes user edits or creates duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.finance.models import (
    AmountCondition,
    Category,
    CategoryKind,
    CategoryRule,
    MatchField,
    MatchType,
)


@dataclass(frozen=True)
class CategorySeed:
    name: str
    kind: str
    description: str = ""
    legacy_name: str = ""
    colour: str = ""
    sort_order: int = 100


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
# ``legacy_name`` is the exact label used in the spreadsheet. It is how historic
# rows are mapped on import, and it must stay stable even if ``name`` changes.

CATEGORIES: tuple[CategorySeed, ...] = (
    # --- Revenue ---
    CategorySeed(
        "Income",
        CategoryKind.REVENUE,
        "General business income, as labelled in the original spreadsheet",
        legacy_name="Income",
        colour="#16A34A",
        sort_order=10,
    ),
    CategorySeed(
        "Shopify Payout",
        CategoryKind.REVENUE,
        "Settlements from the Shopify payment provider (Stripe, previously Adyen). "
        "Net of platform and card fees, so this is cash received rather than gross sales.",
        colour="#22C55E",
        sort_order=11,
    ),
    CategorySeed(
        "Direct Sales Income",
        CategoryKind.REVENUE,
        "Payments taken outside Shopify, e.g. a bank transfer straight from a customer",
        colour="#4ADE80",
        sort_order=12,
    ),
    CategorySeed(
        "Sales Refund",
        CategoryKind.REVENUE,
        "Money refunded to a customer. Recorded against revenue so it reduces sales "
        "rather than inflating expenses.",
        colour="#F97316",
        sort_order=13,
    ),
    CategorySeed(
        "Events",
        CategoryKind.REVENUE,
        "Market stalls and event trading",
        legacy_name="Events",
        colour="#14B8A6",
        sort_order=14,
    ),
    # --- Direct costs ---
    CategorySeed(
        "Apparel",
        CategoryKind.COGS,
        "Print-on-demand supplier costs, principally Inkthreadable",
        legacy_name="Apparel",
        colour="#DC2626",
        sort_order=20,
    ),
    CategorySeed(
        "Supplier refund",
        CategoryKind.COGS,
        "Money back from Inkthreadable (print error, reprint credit). Reduces printer cost; it is not a sale.",
        colour="#B91C1C",
        sort_order=19,
    ),
    CategorySeed(
        "Stock",
        CategoryKind.COGS,
        "Stock purchased and held rather than printed to order",
        legacy_name="Stock",
        colour="#EF4444",
        sort_order=21,
    ),
    CategorySeed(
        "Order",
        CategoryKind.COGS,
        "Other order-related direct costs",
        legacy_name="Order",
        colour="#F87171",
        sort_order=22,
    ),
    CategorySeed(
        "Postage",
        CategoryKind.SHIPPING,
        "Royal Mail, Evri and other despatch costs",
        legacy_name="Postage",
        colour="#EA580C",
        sort_order=23,
    ),
    CategorySeed(
        "Payments Fees",
        CategoryKind.FEES,
        "Card processing and payment provider fees charged separately",
        legacy_name="Payments Fees",
        colour="#D97706",
        sort_order=24,
    ),
    # --- Operating costs ---
    CategorySeed(
        "Store Hosting",
        CategoryKind.OPERATING,
        "Shopify subscription, domains and hosting",
        legacy_name="Store Hosting",
        colour="#7C3AED",
        sort_order=30,
    ),
    CategorySeed(
        "Software",
        CategoryKind.OPERATING,
        "Design and business software subscriptions",
        legacy_name="Software",
        colour="#8B5CF6",
        sort_order=31,
    ),
    CategorySeed(
        "Marketing",
        CategoryKind.OPERATING,
        "Advertising, promotion and social media tooling",
        legacy_name="Marketing",
        colour="#A78BFA",
        sort_order=32,
    ),
    CategorySeed(
        "Stationery",
        CategoryKind.OPERATING,
        "Packaging, labels, printing and office supplies",
        legacy_name="Stationery",
        colour="#C4B5FD",
        sort_order=33,
    ),
    CategorySeed(
        "Expenditure",
        CategoryKind.OPERATING,
        "General business expenditure that does not fit a more specific category",
        legacy_name="Expenditure",
        colour="#64748B",
        sort_order=34,
    ),
    CategorySeed(
        "Tax",
        CategoryKind.OPERATING,
        "HMRC, VAT, corporation tax and other tax payments",
        colour="#57534E",
        sort_order=35,
    ),
    # --- Owner distributions: excluded from the "exc. salary & tithe" view ---
    CategorySeed(
        "Salary",
        CategoryKind.DISTRIBUTION,
        "Owner drawings and wages",
        legacy_name="Salary",
        colour="#0EA5E9",
        sort_order=40,
    ),
    CategorySeed(
        "Tithe",
        CategoryKind.DISTRIBUTION,
        "Charitable giving from business funds",
        legacy_name="Tithe",
        colour="#38BDF8",
        sort_order=41,
    ),
    # --- Outside the profit and loss ---
    CategorySeed(
        "Account Migration",
        CategoryKind.TRANSFER,
        "Moving funds between the old bank account and Monzo. Not income or expenditure.",
        legacy_name="Account Migration",
        colour="#94A3B8",
        sort_order=50,
    ),
    CategorySeed(
        "EXCLUDE",
        CategoryKind.EXCLUDED,
        "Deliberately excluded from all reporting, e.g. personal items and loans",
        legacy_name="EXCLUDE",
        colour="#CBD5E1",
        sort_order=51,
    ),
)


@dataclass(frozen=True)
class RuleSeed:
    pattern: str
    category: str
    name: str = ""
    match_type: str = MatchType.CONTAINS
    match_field: str = MatchField.ANY_TEXT
    amount_condition: str = AmountCondition.ANY
    priority: int = 100


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
# Priorities are spaced by 10 so new rules can be slotted between existing ones
# without renumbering. The first matching rule wins.

# Ported verbatim from the spreadsheet's UNIQUE TEXT → CATEGORY table, in the
# same order, since order determined precedence there too.
LEGACY_RULES: tuple[RuleSeed, ...] = (
    RuleSeed("ROYAL MAIL", "Postage"),
    RuleSeed("INKTHREADABLE", "Apparel"),
    RuleSeed("Phtoshp", "Software"),
    RuleSeed("EVRI", "Postage"),
    RuleSeed("FACEBK", "Marketing"),
    RuleSeed("Google", "Marketing"),
    RuleSeed("Illustrator", "Software"),
    RuleSeed("WIX.COM", "Store Hosting"),
    RuleSeed("CANVA", "Software"),
    RuleSeed("ADYEN N.V.", "Shopify Payout"),
    RuleSeed("HYPER MERCH", "Apparel"),
    RuleSeed("PRODIGI", "Stationery"),
    RuleSeed("NORTH ENGLAND", "Tithe"),
    RuleSeed("LATER", "Marketing"),
    RuleSeed("JELLYFISH", "Marketing"),
    RuleSeed("SOLE TRADER TRA", "Account Migration"),
    RuleSeed("LAURA JEFFERS", "Salary"),
    RuleSeed("LAURA JANE", "Salary"),
    RuleSeed("Adobe", "Software"),
    RuleSeed("WAGES", "Salary"),
    RuleSeed("Asikara", "EXCLUDE"),
    RuleSeed("THABANI SIBANDA", "Salary"),
    RuleSeed("Side hustle", "Salary"),
    RuleSeed("Work", "Salary"),
    RuleSeed("Laura Sibanda", "Salary"),
    RuleSeed("P H Travel Ltd", "EXCLUDE"),
    RuleSeed("Loan", "EXCLUDE"),
    RuleSeed("Zim Travel", "EXCLUDE"),
    RuleSeed("Reserve Pot", "EXCLUDE"),
    RuleSeed("Shopify", "Store Hosting"),
    RuleSeed("Namecheap", "Store Hosting"),
)

# Additions. Each is prefixed "ADDED" in its name so it stands out in the rules
# list and can be switched off without affecting the ported rules.
ADDED_RULES: tuple[RuleSeed, ...] = (
    # Stripe is how Shopify settles takings into the account. It was absent from
    # the spreadsheet's rule table, so every payout had to be categorised by hand.
    # Placed ahead of the ported rules because the narrative also contains
    # "SHOPIFY", which would otherwise match the "Shopify" → Store Hosting rule
    # and misfile revenue as a hosting cost.
    RuleSeed(
        "Stripe Payments",
        "Shopify Payout",
        name="ADDED — Stripe settlement is a Shopify payout, not hosting",
        amount_condition=AmountCondition.POSITIVE,
        priority=10,
    ),
    RuleSeed(
        "INKTHREADABLE",
        "Supplier refund",
        name="ADDED — Inkthreadable credit is a supplier refund, not a sale",
        amount_condition=AmountCondition.POSITIVE,
        priority=15,
    ),
    # The Shopify subscription debit, kept distinct from the payout rule above.
    RuleSeed(
        "SHOPIFY",
        "Store Hosting",
        name="ADDED — Shopify subscription charge",
        amount_condition=AmountCondition.NEGATIVE,
        priority=20,
    ),
    RuleSeed(
        "Facebook",
        "Marketing",
        name="ADDED — Facebook / Meta ads",
        amount_condition=AmountCondition.NEGATIVE,
        priority=30,
    ),
    RuleSeed(
        "HMRC",
        "Tax",
        name="ADDED — HMRC tax payments",
        amount_condition=AmountCondition.NEGATIVE,
        priority=40,
    ),
)

ALL_RULES: tuple[RuleSeed, ...] = ADDED_RULES + tuple(
    # Ported rules start at priority 1000, preserving their relative order.
    RuleSeed(**{**rule.__dict__, "priority": 1000 + index * 10})
    for index, rule in enumerate(LEGACY_RULES)
)


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


@dataclass
class SeedResult:
    categories_created: int = 0
    categories_updated: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    warnings: list[str] = field(default_factory=list)


def seed_categories() -> SeedResult:
    result = SeedResult()
    for spec in CATEGORIES:
        _, created = Category.objects.update_or_create(
            name=spec.name,
            defaults={
                "kind": spec.kind,
                "description": spec.description,
                "legacy_name": spec.legacy_name,
                "colour": spec.colour,
                "sort_order": spec.sort_order,
                "is_active": True,
            },
        )
        if created:
            result.categories_created += 1
        else:
            result.categories_updated += 1
    return result


def seed_rules(result: SeedResult | None = None) -> SeedResult:
    result = result or SeedResult()
    categories = {c.name: c for c in Category.objects.all()}

    for spec in ALL_RULES:
        category = categories.get(spec.category)
        if category is None:
            result.warnings.append(
                f"Rule for {spec.pattern!r} references unknown category {spec.category!r} and was skipped"
            )
            continue

        # Keyed on the rule's identity (what it matches), so re-seeding updates
        # the existing rule rather than adding a near-duplicate.
        _, created = CategoryRule.objects.update_or_create(
            pattern=spec.pattern,
            match_type=spec.match_type,
            match_field=spec.match_field,
            amount_condition=spec.amount_condition,
            defaults={
                "category": category,
                "priority": spec.priority,
                "name": spec.name,
                "is_active": True,
            },
        )
        if created:
            result.rules_created += 1
        else:
            result.rules_updated += 1
    return result


def seed_all() -> SeedResult:
    result = seed_categories()
    return seed_rules(result)
