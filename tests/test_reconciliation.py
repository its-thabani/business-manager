"""Shopify sales days and supplier invoices matched to bank cash."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.analytics.reconciliation import (
    apply_suggestions,
    is_payout,
    match_shopify_days,
    match_supplier_charges,
    persist_link,
    reconcile,
    sale_days,
)
from apps.core.periods import DateRange
from apps.finance.models import CashLink, CashLinkConfidence, CashLinkKind
from apps.supplier.models import SupplierOrder

pytestmark = pytest.mark.django_db


def _range() -> DateRange:
    return DateRange(date(2026, 1, 1), date(2026, 12, 31), "2026")


def test_stripe_income_rows_count_as_payouts(make_txn, category_by_name):
    stripe = make_txn(
        "67.51",
        counterparty="Stripe",
        category=category_by_name("Income"),
        external_id="tx_stripe_1",
    )
    noise = make_txn(
        "20.00",
        counterparty="A customer",
        category=category_by_name("Income"),
        external_id="tx_other_1",
    )

    assert is_payout(stripe) is True
    assert is_payout(noise) is False


def test_sales_day_matches_a_stripe_deposit_within_a_week(
    make_product, make_order, make_txn, category_by_name
):
    product = make_product(variants=[("Black", "L", "34.99")])
    make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        shipping_charged="0.00",
        payment_fee="0.48",
        when=date(2026, 1, 3),
    )
    payout = make_txn(
        "34.51",
        counterparty="Stripe",
        category=category_by_name("Income"),
        when=date(2026, 1, 7),
        external_id="tx_match",
    )

    days = sale_days(_range())
    matches = match_shopify_days(days, [payout])

    assert len(matches) == 1
    assert matches[0].sale_on == date(2026, 1, 3)
    assert matches[0].expected_amount == Decimal("34.51")
    assert matches[0].bank_transaction == payout


def test_close_enough_amounts_match_and_far_amounts_do_not(
    make_product, make_order, make_txn, category_by_name
):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="1.00",
        when=date(2026, 2, 1),
    )
    near = make_txn(
        "19.04",
        counterparty="Stripe",
        when=date(2026, 2, 4),
        external_id="tx_near",
        category=category_by_name("Income"),
    )
    far = make_txn(
        "12.34",
        counterparty="Stripe",
        when=date(2026, 2, 5),
        external_id="tx_far",
        category=category_by_name("Income"),
    )

    days = sale_days(_range())
    matches = match_shopify_days(days, [near, far])

    assert [row.bank_transaction for row in matches] == [near]


def test_consecutive_days_batch_to_one_payout(make_product, make_order, make_txn, category_by_name):
    product = make_product(variants=[("Black", "L", "10.00")])
    variant = product.variants.get()
    make_order(
        lines=[(variant, 1, "10.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
        when=date(2026, 3, 1),
    )
    make_order(
        lines=[(variant, 2, "10.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
        when=date(2026, 3, 2),
    )
    payout = make_txn(
        "30.00",
        counterparty="Stripe",
        when=date(2026, 3, 6),
        external_id="tx_batch",
        category=category_by_name("Income"),
    )

    matches = match_shopify_days(sale_days(_range()), [payout])

    assert len(matches) == 1
    assert matches[0].sale_dates == [date(2026, 3, 1), date(2026, 3, 2)]
    assert "batch" in matches[0].reason


def test_confirmed_shopify_link_is_not_overwritten(
    make_product, make_order, make_txn, category_by_name
):
    product = make_product(variants=[("Black", "L", "40.00")])
    make_order(
        lines=[(product.variants.get(), 1, "40.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
        when=date(2026, 4, 1),
    )
    first = make_txn(
        "40.00",
        counterparty="Stripe",
        when=date(2026, 4, 4),
        external_id="tx_first",
        category=category_by_name("Income"),
    )
    later = make_txn(
        "40.00",
        counterparty="Stripe",
        when=date(2026, 4, 5),
        external_id="tx_later",
        category=category_by_name("Income"),
    )
    CashLink.objects.create(
        bank_transaction=later,
        kind=CashLinkKind.SHOPIFY_DAY,
        confidence=CashLinkConfidence.CONFIRMED,
        sale_on=date(2026, 4, 1),
        expected_amount=Decimal("40.00"),
        match_reason="kept by hand",
    )

    apply_suggestions()

    later.refresh_from_db()
    assert later.cash_link.is_confirmed
    assert later.cash_link.sale_on == date(2026, 4, 1)
    assert not CashLink.objects.filter(bank_transaction=first).exists()


def test_old_supplier_invoice_does_not_match_a_recent_charge(make_txn):
    old = SupplierOrder.objects.create(
        supplier_reference="INK-OLD",
        placed_at=timezone.make_aware(datetime(2021, 3, 1, 12, 0)),
        total_cost=Decimal("45.00"),
    )
    recent = make_txn(
        "-45.00",
        counterparty="Inkthreadable",
        when=date(2026, 2, 10),
        external_id="tx_ink_new",
    )

    assert match_supplier_charges([recent], [old]) == []


def test_supplier_invoice_matches_a_nearby_bank_charge(make_txn):
    invoice = SupplierOrder.objects.create(
        supplier_reference="INK-45",
        placed_at=timezone.make_aware(datetime(2021, 3, 1, 12, 0)),
        total_cost=Decimal("45.00"),
    )
    charge = make_txn(
        "-45.00",
        counterparty="Inkthreadable",
        when=date(2021, 3, 4),
        external_id="tx_ink_old",
    )

    matches = match_supplier_charges([charge], [invoice])

    assert len(matches) == 1
    assert matches[0].supplier_order == invoice
    assert matches[0].kind == CashLinkKind.SUPPLIER_ORDER


def test_period_report_keeps_unmatched_rows_visible(
    make_product, make_order, make_txn, category_by_name
):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="1.00",
        when=date(2026, 5, 1),
    )
    make_txn(
        "19.00",
        counterparty="Stripe",
        when=date(2026, 5, 4),
        external_id="tx_ok",
        category=category_by_name("Income"),
    )
    leftover = make_txn(
        "311.06",
        counterparty="Stripe",
        when=date(2026, 5, 10),
        external_id="tx_batch_left",
        category=category_by_name("Income"),
    )
    make_txn(
        "-22.00",
        counterparty="Inkthreadable",
        when=date(2026, 5, 12),
        external_id="tx_ink_2026",
    )

    report = reconcile(_range())

    assert report.expected_cash == Decimal("19.00")
    assert report.bank_received == Decimal("330.06")
    assert report.cash_gap == Decimal("-311.06")
    assert len(report.shopify_matches) == 1
    assert leftover in report.unmatched_payouts
    assert len(report.unmatched_charges) == 1
    assert any("unmatched" in note.lower() or "cannot be matched" in note.lower() for note in report.notes)


def test_persist_refuses_to_clobber_a_confirmed_link(make_txn, category_by_name, make_product, make_order):
    product = make_product(variants=[("Black", "L", "15.00")])
    make_order(
        lines=[(product.variants.get(), 1, "15.00")],
        shipping_charged="0.00",
        payment_fee="0.00",
        when=date(2026, 6, 1),
    )
    txn = make_txn(
        "15.00",
        counterparty="Stripe",
        when=date(2026, 6, 4),
        external_id="tx_keep",
        category=category_by_name("Income"),
    )
    saved = CashLink.objects.create(
        bank_transaction=txn,
        kind=CashLinkKind.SHOPIFY_DAY,
        confidence=CashLinkConfidence.CONFIRMED,
        sale_on=date(2026, 1, 1),
        match_reason="manual",
        expected_amount=Decimal("15.00"),
    )
    suggestion = match_shopify_days(sale_days(_range()), [txn])[0]

    persist_link(suggestion)

    saved.refresh_from_db()
    assert saved.sale_on == date(2026, 1, 1)
    assert saved.is_confirmed


def test_reconciliation_page_shows_the_gap(client, make_product, make_order, make_txn, category_by_name):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="1.00",
        when=date(2026, 7, 1),
    )
    make_txn(
        "19.00",
        counterparty="Stripe",
        when=date(2026, 7, 5),
        external_id="tx_page",
        category=category_by_name("Income"),
    )

    response = client.get("/reconciliation/?range=all")

    assert response.status_code == 200
    assert b"Expected after fees" in response.content
    assert b"19.00" in response.content
    assert b"suggested" in response.content
    assert b"Monthly sales vs payouts" in response.content


def test_confirming_a_suggestion_from_the_page(
    client, make_product, make_order, make_txn, category_by_name
):
    product = make_product(variants=[("Black", "L", "20.00")])
    make_order(
        lines=[(product.variants.get(), 1, "20.00")],
        shipping_charged="0.00",
        payment_fee="1.00",
        when=date(2026, 8, 1),
    )
    txn = make_txn(
        "19.00",
        counterparty="Stripe",
        when=date(2026, 8, 4),
        external_id="tx_confirm",
        category=category_by_name("Income"),
    )

    response = client.post(
        "/reconciliation/?range=all",
        {"action": "confirm", "transaction_id": str(txn.pk), "kind": CashLinkKind.SHOPIFY_DAY},
    )

    assert response.status_code == 302
    link = CashLink.objects.get(bank_transaction=txn)
    assert link.is_confirmed
    assert link.sale_on == date(2026, 8, 1)
