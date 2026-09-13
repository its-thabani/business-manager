"""Deduplication is the guarantee that makes repeated CSV imports safe.

These tests pin down the two cases that matter: an overlapping export must not
create duplicates, and two genuinely separate payments that happen to look
identical must not be collapsed into one.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.finance.models import BankTransaction


def fingerprint(account, **kwargs):
    defaults = {
        "account_id": account.pk,
        "external_id": "",
        "occurred_on": date(2026, 6, 15),
        "amount": Decimal("-22.79"),
        "counterparty": "Inkthreadable",
        "description": "",
    }
    return BankTransaction.build_fingerprint(**{**defaults, **kwargs})


class TestFingerprint:
    def test_bank_id_alone_identifies_a_transaction(self, account):
        # Monzo tidies up merchant names between exports, so the same transaction
        # can arrive with different text. The ID must still match.
        first = fingerprint(account, external_id="mm_123", counterparty="Inkthreadable")
        second = fingerprint(account, external_id="mm_123", counterparty="INKTHREADABLE BLACKBURN GBR")
        assert first == second

    def test_different_ids_never_collide(self, account):
        assert fingerprint(account, external_id="mm_123") != fingerprint(account, external_id="mm_124")

    def test_same_content_in_different_accounts_is_distinct(self, account, db):
        from apps.finance.models import BankAccount

        other = BankAccount.objects.create(name="Other Account")
        assert fingerprint(account) != fingerprint(other)

    def test_whitespace_and_case_in_the_narrative_are_ignored(self, account):
        # Bank narratives pad fields with runs of spaces and tabs.
        padded = fingerprint(account, counterparty="INKTHREADABLE     \t  BLACKBURN")
        tidy = fingerprint(account, counterparty="inkthreadable blackburn")
        assert padded == tidy

    def test_genuine_repeat_payments_are_kept_apart(self, account):
        # Two Inkthreadable orders of the same value on the same day are ordinary
        # for print-on-demand and are two real payments.
        assert fingerprint(account, occurrence=1) != fingerprint(account, occurrence=2)

    def test_amount_is_part_of_the_content_hash(self, account):
        assert fingerprint(account, amount=Decimal("-22.79")) != fingerprint(account, amount=Decimal("-22.80"))

    def test_date_is_part_of_the_content_hash(self, account):
        assert fingerprint(account, occurred_on=date(2026, 6, 15)) != fingerprint(
            account, occurred_on=date(2026, 6, 16)
        )


class TestUniqueConstraint:
    def test_the_database_refuses_a_duplicate_fingerprint(self, account, make_txn):
        from django.db.utils import IntegrityError

        make_txn("-22.79", counterparty="Inkthreadable", external_id="mm_123")
        with pytest.raises(IntegrityError):
            make_txn("-22.79", counterparty="Inkthreadable", external_id="mm_123")
