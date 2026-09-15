"""Tests for the categorisation rule engine."""

from datetime import date

import pytest

from apps.finance.categorisation import categorise
from apps.finance.models import (
    AmountCondition,
    BankTransaction,
    Category,
    CategoryKind,
    CategoryRule,
    CategorySource,
    MatchField,
    MatchType,
)


@pytest.mark.django_db
class TestRuleMatching:
    def test_contains_match_is_case_insensitive(self, seeded, make_txn):
        txn = make_txn("-13.96", counterparty="inkthreadable")
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"

    def test_padded_bank_narrative_still_matches(self, seeded, make_txn):
        txn = make_txn("-13.96", description="INKTHREADABLE          BLACKBURN     GBR")
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"

    def test_tab_separated_legacy_narrative_still_matches(self, seeded, make_txn):
        txn = make_txn("-43.99", description="INKTHREADABLE         \tON 12 MAY BCC")
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"

    def test_unmatched_transaction_is_left_uncategorised_not_guessed(self, seeded, make_txn):
        txn = make_txn("45.00", counterparty="Some Unknown Customer")
        result = categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category is None
        assert result.uncategorised == 1

    def test_lower_priority_number_wins(self, seeded, make_txn):
        apparel = Category.objects.get(name="Apparel")
        software = Category.objects.get(name="Software")
        CategoryRule.objects.create(pattern="ACME", category=software, priority=5)
        CategoryRule.objects.create(pattern="ACME", category=apparel, priority=50)

        txn = make_txn("-10.00", counterparty="ACME Ltd")
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category.name == "Software"

    def test_inactive_rules_are_ignored(self, seeded, make_txn):
        CategoryRule.objects.filter(pattern="INKTHREADABLE").update(is_active=False)
        txn = make_txn("-13.96", counterparty="Inkthreadable")
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category is None

    @pytest.mark.parametrize(
        "match_type,pattern,text,expected",
        [
            (MatchType.EXACT, "Canva", "Canva", True),
            (MatchType.EXACT, "Canva", "Canva Pro", False),
            (MatchType.STARTS_WITH, "ROYAL", "ROYAL MAIL GROUP", True),
            (MatchType.STARTS_WITH, "MAIL", "ROYAL MAIL GROUP", False),
            (MatchType.ENDS_WITH, "GBR", "INKTHREADABLE BLACKBURN GBR", True),
            (MatchType.REGEX, r"SHOPIFY\*\s*\d+", "SHOPIFY* 578454488 DUBLIN IRL", True),
            (MatchType.NOT_CONTAINS, "Stripe", "Adyen N.V.", True),
        ],
    )
    def test_match_types(self, seeded, make_txn, match_type, pattern, text, expected):
        stationery = Category.objects.get(name="Stationery")
        CategoryRule.objects.create(pattern=pattern, match_type=match_type, category=stationery, priority=1)
        txn = make_txn("-10.00", counterparty=text)
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert (txn.category == stationery) is expected

    def test_an_invalid_regex_does_not_break_the_run(self, seeded, make_txn):
        CategoryRule.objects.create(
            pattern="([unclosed", match_type=MatchType.REGEX,
            category=Category.objects.get(name="Software"), priority=1,
        )
        txn = make_txn("-13.96", counterparty="Inkthreadable")
        categorise(BankTransaction.objects.filter(pk=txn.pk))
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"

    def test_match_field_restricts_where_a_rule_looks(self, seeded, make_txn):
        CategoryRule.objects.create(
            pattern="REFERENCE-ONLY", match_field=MatchField.NOTES,
            category=Category.objects.get(name="Tithe"), priority=1,
        )
        in_notes = make_txn("-10.00", notes="REFERENCE-ONLY")
        in_name = make_txn("-11.00", counterparty="REFERENCE-ONLY")
        categorise()
        in_notes.refresh_from_db()
        in_name.refresh_from_db()
        assert in_notes.category.name == "Tithe"
        assert in_name.category is None


@pytest.mark.django_db
class TestAmountConditions:
    def test_a_shopify_payout_is_revenue_and_the_subscription_is_a_cost(self, seeded, make_txn):
        # Both narratives contain "SHOPIFY"; only the sign tells them apart.
        payout = make_txn("28.15", counterparty="Stripe Payments UK Ltd", notes="SHOPIFY")
        subscription = make_txn("-25.00", counterparty="Shopify", description="SHOPIFY* 578454488 DUBLIN IRL")
        categorise()
        payout.refresh_from_db()
        subscription.refresh_from_db()
        assert payout.category.name == "Shopify Payout"
        assert payout.category.kind == CategoryKind.REVENUE
        assert subscription.category.name == "Store Hosting"
        assert subscription.category.kind == CategoryKind.OPERATING

    def test_inkthreadable_credit_is_a_supplier_refund_not_a_sale(self, seeded, make_txn):
        credit = make_txn("12.00", counterparty="Inkthreadable")
        debit = make_txn("-12.00", counterparty="Inkthreadable")
        categorise()
        credit.refresh_from_db()
        debit.refresh_from_db()
        assert credit.category.name == "Supplier refund"
        assert credit.category.kind == CategoryKind.COGS
        assert debit.category.name == "Apparel"

    def test_positive_only_rule_ignores_debits(self, seeded, make_txn):
        rule_category = Category.objects.get(name="Events")
        CategoryRule.objects.create(
            pattern="MARKET", amount_condition=AmountCondition.POSITIVE,
            category=rule_category, priority=1,
        )
        credit = make_txn("50.00", counterparty="MARKET STALL")
        debit = make_txn("-50.00", counterparty="MARKET STALL")
        categorise()
        credit.refresh_from_db()
        debit.refresh_from_db()
        assert credit.category == rule_category
        assert debit.category is None


@pytest.mark.django_db
class TestLocksAndReruns:
    def test_a_manual_category_is_not_overwritten(self, seeded, make_txn):
        txn = make_txn("-13.96", counterparty="Inkthreadable")
        txn.set_manual_category(Category.objects.get(name="Stationery"))

        result = categorise()
        txn.refresh_from_db()
        assert txn.category.name == "Stationery"
        assert result.locked_skipped == 1

    def test_locks_can_be_deliberately_overridden(self, seeded, make_txn):
        txn = make_txn("-13.96", counterparty="Inkthreadable")
        txn.set_manual_category(Category.objects.get(name="Stationery"))
        categorise(respect_locks=False)
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"

    def test_only_uncategorised_leaves_existing_classifications_alone(self, seeded, make_txn):
        txn = make_txn("-13.96", counterparty="Inkthreadable")
        txn.category = Category.objects.get(name="Software")
        txn.category_source = CategorySource.RULE
        txn.save()

        categorise(only_uncategorised=True)
        txn.refresh_from_db()
        assert txn.category.name == "Software"

    def test_removing_a_rule_clears_the_category_it_had_assigned(self, seeded, make_txn):
        txn = make_txn("-13.96", counterparty="Inkthreadable")
        categorise()
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"

        CategoryRule.objects.filter(pattern="INKTHREADABLE").delete()
        categorise()
        txn.refresh_from_db()
        assert txn.category is None
        assert txn.category_source == CategorySource.UNCATEGORISED

    def test_imported_spreadsheet_categories_are_kept_when_no_rule_matches(self, seeded, make_txn):
        txn = make_txn(
            "-13.96",
            counterparty="One-off shop with no rule",
            category=Category.objects.get(name="Stationery"),
            category_source=CategorySource.IMPORTED,
            is_category_locked=True,
        )
        categorise()
        txn.refresh_from_db()
        assert txn.category.name == "Stationery"
        assert txn.category_source == CategorySource.IMPORTED

    def test_apply_rules_replaces_imported_expenditure_when_hmrc_matches(self, seeded, make_txn):
        txn = make_txn(
            "-1.81",
            counterparty="HMRC",
            when=date(2026, 4, 13),
            category=Category.objects.get(name="Expenditure"),
            category_source=CategorySource.IMPORTED,
            is_category_locked=True,
        )
        result = categorise()
        txn.refresh_from_db()
        assert txn.category.name == "Tax"
        assert txn.category_source == CategorySource.RULE
        assert txn.is_category_locked is False
        assert result.changed == 1

    def test_apply_rules_labels_facebook_ads_as_marketing(self, seeded, make_txn):
        txn = make_txn(
            "-12.00",
            counterparty="Facebook",
            category=Category.objects.get(name="Expenditure"),
            category_source=CategorySource.IMPORTED,
            is_category_locked=True,
        )
        categorise()
        txn.refresh_from_db()
        assert txn.category.name == "Marketing"

    def test_rerunning_is_idempotent(self, seeded, make_txn):
        make_txn("-13.96", counterparty="Inkthreadable")
        categorise()
        second = categorise()
        assert second.changed == 0

    def test_rule_usage_is_recorded(self, seeded, make_txn):
        make_txn("-13.96", counterparty="Inkthreadable", when=date(2026, 1, 5))
        make_txn("-20.00", counterparty="Inkthreadable", when=date(2026, 1, 6))
        categorise()
        rule = CategoryRule.objects.get(pattern="INKTHREADABLE", category__name="Apparel")
        assert rule.match_count == 2
        assert rule.last_matched_at is not None
