"""Tests for the Monzo CSV importer, using fixtures modelled on the real export."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.finance.importers import import_monzo_csv
from apps.finance.models import BankTransaction

HEADER = (
    "Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,Local amount,"
    "Local currency,Notes and #tags,Address,Receipt,Description,Category split,"
    "Money Out,Money In,Balance,Balance currency\n"
)

ROWS = [
    # A supplier card payment.
    "mm_0000B9BkWNnhkmODkzu41Z,09/08/2026,07:05:03,Card payment,Inkthreadable,👕,Sales,-13.96,GBP,"
    "-13.96,GBP,,,,INKTHREADABLE          BLACKBURN     GBR,,-13.96,,972.94,GBP",
    # A Shopify payout arriving via Stripe.
    "mm_0000B9HymJxGNt9ij8J197,12/08/2026,07:13:04,Faster payment,Stripe Payments UK Ltd,,Income,28.15,GBP,"
    "28.15,GBP,SHOPIFY,,,SHOPIFY,,,28.15,966.04,GBP",
    # A foreign-currency card payment: charged in USD, settled in GBP.
    "mm_0000B93kZujiCa4EekT4hG,05/08/2026,10:28:00,Card payment,Namecheap,🌐,Bills,-7.44,GBP,"
    "-9.98,USD,,,,NAME-CHEAP.COM* GZRVGK PHOENIX       USA,,-7.44,,986.90,GBP",
]


def write_csv(tmp_path, rows, name="monzo.csv"):
    path = tmp_path / name
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.mark.django_db
class TestMonzoImport:
    def test_imports_all_rows(self, tmp_path, account):
        report = import_monzo_csv(write_csv(tmp_path, ROWS), account=account)
        assert (report.rows_read, report.created, report.duplicates) == (3, 3, 0)
        assert BankTransaction.objects.count() == 3

    def test_dates_are_read_as_day_first(self, tmp_path, account):
        # 09/08/2026 is 9 August, not 8 September.
        import_monzo_csv(write_csv(tmp_path, [ROWS[0]]), account=account)
        assert BankTransaction.objects.get().occurred_on == date(2026, 8, 9)

    def test_amount_sign_is_preserved(self, tmp_path, account):
        import_monzo_csv(write_csv(tmp_path, ROWS), account=account)
        supplier = BankTransaction.objects.get(counterparty="Inkthreadable")
        payout = BankTransaction.objects.get(counterparty="Stripe Payments UK Ltd")
        assert supplier.amount == Decimal("-13.96")
        assert payout.amount == Decimal("28.15")

    def test_foreign_currency_detail_is_kept(self, tmp_path, account):
        import_monzo_csv(write_csv(tmp_path, ROWS), account=account)
        txn = BankTransaction.objects.get(counterparty="Namecheap")
        assert txn.amount == Decimal("-7.44")
        assert (txn.local_amount, txn.local_currency) == (Decimal("-9.98"), "USD")

    def test_raw_row_is_preserved_in_full(self, tmp_path, account):
        import_monzo_csv(write_csv(tmp_path, [ROWS[0]]), account=account)
        raw = BankTransaction.objects.get().raw
        assert raw["Transaction ID"] == "mm_0000B9BkWNnhkmODkzu41Z"
        assert raw["Emoji"] == "👕"

    def test_monzo_own_category_is_stored_but_not_used_for_classification(self, tmp_path, account):
        # Monzo labels Inkthreadable card payments "Sales", which is misleading.
        import_monzo_csv(write_csv(tmp_path, [ROWS[0]]), account=account)
        txn = BankTransaction.objects.get()
        assert txn.bank_category == "Sales"
        assert txn.category is None

    def test_time_is_combined_into_a_timestamp(self, tmp_path, account):
        # Monzo exports wall-clock local time. 07:05 on 9 August is BST, so it is
        # stored as 06:05 UTC and must read back as 07:05 in London.
        from django.utils import timezone

        import_monzo_csv(write_csv(tmp_path, [ROWS[0]]), account=account)
        txn = BankTransaction.objects.get()
        assert txn.occurred_at is not None
        local = timezone.localtime(txn.occurred_at)
        assert (local.hour, local.minute) == (7, 5)

    def test_the_reporting_date_stays_on_the_local_calendar_day(self, tmp_path, account):
        # A late-evening BST transaction must not slide into the next UTC day, or
        # it would land in the wrong month at a month boundary.
        row = (
            "mm_LATE,31/07/2026,23:45:00,Card payment,Inkthreadable,,Sales,-19.00,GBP,-19.00,GBP,,,,"
            "IT,,-19.00,,100.00,GBP"
        )
        import_monzo_csv(write_csv(tmp_path, [row]), account=account)
        assert BankTransaction.objects.get().occurred_on == date(2026, 7, 31)


@pytest.mark.django_db
class TestReimportSafety:
    def test_reimporting_the_same_file_creates_nothing(self, tmp_path, account):
        path = write_csv(tmp_path, ROWS)
        import_monzo_csv(path, account=account)
        second = import_monzo_csv(path, account=account)
        assert (second.created, second.duplicates) == (0, 3)
        assert BankTransaction.objects.count() == 3

    def test_overlapping_exports_only_add_the_new_rows(self, tmp_path, account):
        import_monzo_csv(write_csv(tmp_path, ROWS[:2], "first.csv"), account=account)
        report = import_monzo_csv(write_csv(tmp_path, ROWS, "second.csv"), account=account)
        assert (report.created, report.duplicates) == (1, 2)
        assert BankTransaction.objects.count() == 3

    def test_a_manual_category_survives_reimport(self, tmp_path, account, category_by_name):
        path = write_csv(tmp_path, ROWS)
        import_monzo_csv(path, account=account)
        txn = BankTransaction.objects.get(counterparty="Inkthreadable")
        txn.set_manual_category(category_by_name("Apparel"))

        import_monzo_csv(path, account=account)
        txn.refresh_from_db()
        assert txn.category.name == "Apparel"
        assert txn.is_category_locked

    def test_two_identical_payments_on_one_day_are_both_kept(self, tmp_path, account):
        # Same amount, same merchant, same day, but different transaction IDs.
        rows = [
            "mm_AAA,14/08/2026,08:00:00,Card payment,Inkthreadable,👕,Sales,-22.79,GBP,-22.79,GBP,,,,"
            "INKTHREADABLE BLACKBURN GBR,,-22.79,,100.00,GBP",
            "mm_BBB,14/08/2026,09:00:00,Card payment,Inkthreadable,👕,Sales,-22.79,GBP,-22.79,GBP,,,,"
            "INKTHREADABLE BLACKBURN GBR,,-22.79,,77.21,GBP",
        ]
        report = import_monzo_csv(write_csv(tmp_path, rows), account=account)
        assert report.created == 2

    def test_identical_rows_with_no_id_are_both_kept(self, tmp_path, account):
        rows = [
            ",14/08/2026,08:00:00,Card payment,Inkthreadable,,Sales,-22.79,GBP,-22.79,GBP,,,,IT,,-22.79,,100.00,GBP",
            ",14/08/2026,08:00:00,Card payment,Inkthreadable,,Sales,-22.79,GBP,-22.79,GBP,,,,IT,,-22.79,,77.21,GBP",
        ]
        path = write_csv(tmp_path, rows)
        assert import_monzo_csv(path, account=account).created == 2
        # ...and re-importing that same file still adds nothing.
        assert import_monzo_csv(path, account=account).created == 0


@pytest.mark.django_db
class TestMalformedInput:
    def test_dry_run_writes_nothing(self, tmp_path, account):
        report = import_monzo_csv(write_csv(tmp_path, ROWS), account=account, dry_run=True)
        assert report.created == 3
        assert BankTransaction.objects.count() == 0

    def test_a_bad_row_is_skipped_and_reported_without_losing_the_rest(self, tmp_path, account):
        rows = [ROWS[0], "mm_BAD,not-a-date,,Card payment,Broken,,,-1.00,GBP,,,,,,X,,,,,GBP", ROWS[1]]
        report = import_monzo_csv(write_csv(tmp_path, rows), account=account)
        assert report.created == 2
        assert report.skipped == 1
        assert "unreadable date" in report.issues[0].reason

    def test_a_row_with_no_amount_is_skipped(self, tmp_path, account):
        rows = ["mm_X,09/08/2026,07:05:03,Card payment,Nothing,,,,GBP,,GBP,,,,X,,,,,GBP"]
        report = import_monzo_csv(write_csv(tmp_path, rows), account=account)
        assert (report.created, report.skipped) == (0, 1)

    def test_a_non_monzo_file_is_rejected_with_a_clear_message(self, tmp_path, account):
        path = tmp_path / "wrong.csv"
        path.write_text("Foo,Bar\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="does not look like a Monzo export"):
            import_monzo_csv(path, account=account)

    def test_a_byte_order_mark_does_not_break_the_first_column(self, tmp_path, account):
        path = tmp_path / "bom.csv"
        path.write_text("\ufeff" + HEADER + ROWS[0] + "\n", encoding="utf-8")
        import_monzo_csv(path, account=account)
        assert BankTransaction.objects.get().external_id == "mm_0000B9BkWNnhkmODkzu41Z"
