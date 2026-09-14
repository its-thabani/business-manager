"""Product analytics pages, cash charts, and the Inkthreadable catalogue."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import ProductGroup
from apps.finance.models import CategoryRule, CategorySource
from apps.sales.models import Customer
from apps.supplier.models import SupplierOrder
from apps.web.charts import column_chart, share_bars

pytestmark = pytest.mark.django_db


def test_column_chart_scales_against_the_tallest_bar():
    rows = column_chart(
        [
            {"label": "Jan", "revenue": Decimal("100.00"), "profit": Decimal("50.00")},
            {"label": "Feb", "revenue": Decimal("50.00"), "profit": Decimal("-25.00")},
        ],
        ["revenue", "profit"],
    )

    assert rows[0]["revenue_pct"] == 100
    assert rows[1]["revenue_pct"] == 50
    assert rows[1]["profit_neg"] is True


def test_share_bars_keep_the_largest_item_at_full_width():
    rows = share_bars(
        [{"label": "A", "units": 10}, {"label": "B", "units": 5}, {"label": "C", "units": 1}],
        value_key="units",
        limit=2,
    )

    assert [row["label"] for row in rows] == ["A", "B"]
    assert rows[0]["bar_pct"] == 100
    assert rows[1]["bar_pct"] == 50


def test_cash_dashboard_includes_the_monthly_chart(client, make_txn, category_by_name):
    make_txn("40.00", category=category_by_name("Shopify Payout"), counterparty="Shopify")

    response = client.get("/?range=ytd")

    assert response.status_code == 200
    assert b"Profit and revenue over time" in response.content
    assert b"Where the money went" in response.content
    assert b"excluding salary" in response.content


def test_cash_dashboard_can_include_salary_and_tithe(client, make_txn, category_by_name):
    make_txn("100.00", category=category_by_name("Shopify Payout"), counterparty="Stripe")
    make_txn("-20.00", category=category_by_name("Salary"), counterparty="Wages")

    hidden = client.get("/?range=all")
    shown = client.get("/?range=all&drawings=1")

    assert hidden.status_code == shown.status_code == 200
    assert b"Expenses (exc. salary" in hidden.content
    assert b"Include salary" in hidden.content
    assert b"including salary and tithe" in shown.content


def test_cash_and_expenses_never_name_an_excluded_category(client, make_txn, category_by_name):
    make_txn("40.00", category=category_by_name("Shopify Payout"), counterparty="Stripe")
    make_txn("-12.00", category=category_by_name("Apparel"), counterparty="Inkthreadable")
    make_txn("-500.00", category=category_by_name("EXCLUDE"), counterparty="Personal")
    make_txn("-80.00", category=category_by_name("Account Migration"), counterparty="Transfer")

    cash = client.get("/?range=all")
    spend = client.get("/expenses/?range=all")

    assert cash.status_code == spend.status_code == 200
    assert b"EXCLUDE" not in cash.content
    assert b"Account Migration" not in cash.content
    assert b"EXCLUDE" not in spend.content
    assert b"Account Migration" not in spend.content
    assert b"Apparel" in spend.content
    assert b"Inkthreadable" in spend.content
    assert b"Personal" not in spend.content


def test_expenses_page_lists_every_reportable_outgoing(client, make_txn, category_by_name):
    make_txn("-25.00", category=category_by_name("Store Hosting"), counterparty="Shopify")
    make_txn("-50.00", category=category_by_name("Salary"), counterparty="Wages")
    make_txn("-29.99", category=category_by_name("Sales Refund"), counterparty="Customer")

    shown = client.get("/expenses/?range=all")
    hidden = client.get("/expenses/?range=all&drawings=0")

    assert shown.status_code == hidden.status_code == 200
    assert b"True expenses" in shown.content
    assert b"Shopify" in shown.content
    assert b"Wages" in shown.content
    assert b"Wages" not in hidden.content
    assert b"reduce sales" in shown.content
    assert b'name="category"' in shown.content
    assert b"Set" in shown.content


def test_expenses_page_can_override_a_row_category(client, make_txn, category_by_name):
    txn = make_txn("-1.81", category=category_by_name("Expenditure"), counterparty="HMRC")
    tax = category_by_name("Tax")

    response = client.post(
        "/expenses/?range=all",
        {"action": "set_category", "txn_id": str(txn.pk), "category": str(tax.pk)},
    )

    assert response.status_code == 302
    txn.refresh_from_db()
    assert txn.category_id == tax.pk
    assert txn.category_source == CategorySource.MANUAL
    assert txn.is_category_locked is True


def test_apply_rules_from_categories_recategorises_imported_hmrc(client, make_txn, category_by_name):
    txn = make_txn(
        "-1.81",
        counterparty="HMRC",
        category=category_by_name("Expenditure"),
        category_source=CategorySource.IMPORTED,
        is_category_locked=True,
    )

    response = client.post("/categories/", {"action": "apply"})

    assert response.status_code == 302
    txn.refresh_from_db()
    assert txn.category.name == "Tax"
    assert txn.category_source == CategorySource.RULE
    assert txn.is_category_locked is False


def test_cash_dashboard_does_not_offer_a_spreadsheet_mode(client, make_txn, category_by_name):
    make_txn("100.00", category=category_by_name("Income"), counterparty="Stripe")
    make_txn("30.00", category=category_by_name("Salary"), counterparty="Laura Sibanda")
    make_txn("21.73", category=category_by_name("Apparel"), counterparty="Inkthreadable")

    response = client.get("/?range=all")

    assert response.status_code == 200
    assert b"Match spreadsheet" not in response.content
    assert b"This system" not in response.content
    assert b"classifies some credits as income" not in response.content
    assert b"sort=total" in response.content


def test_insights_page_lists_numbered_findings_and_the_wix_cutoff(
    client, make_product, make_order, make_txn, category_by_name
):
    product = make_product(title="Lifeline Hoodie", variants=[("Black", "L", "34.99")])
    make_order(lines=[(product.variants.get(), 2, "34.99")])
    make_txn("40.00", category=category_by_name("Income"), counterparty="Stripe")
    make_txn("15.00", category=category_by_name("Income"), counterparty="Wix", when=date(2025, 3, 1))

    response = client.get("/insights/?range=all")

    assert response.status_code == 200
    assert b"Insights" in response.content
    assert b"Lifeline Hoodie" in response.content
    assert b"Wix" in response.content


def test_products_page_ranks_what_sold_and_withholds_incomplete_profit(
    client, make_product, map_variant, make_order
):
    product = make_product(title="Lifeline Hoodie", variants=[("Black", "L", "34.99")])
    map_variant(product.variants.get(), cost="12.00")
    make_order(lines=[(product.variants.get(), 3, "34.99")])
    mystery = make_product(title="Mystery Tee", variants=[("White", "M", "24.99")])
    make_order(lines=[(mystery.variants.get(), 1, "24.99")])

    response = client.get("/products/?range=all")

    assert response.status_code == 200
    assert b"Lifeline Hoodie" in response.content
    assert b"Mystery Tee" in response.content
    assert b"Approximate, not the books" in response.content
    assert b"Approx. contribution profit" in response.content
    assert f"/mapping/{mystery.pk}/".encode() in response.content
    assert b"add cost" in response.content
    assert b"sort=orders" in response.content
    assert b"Top products by revenue" in response.content
    assert b"sort=name" in response.content


def test_products_page_hides_free_downloads_by_default(client, make_product, make_order):
    paid = make_product(title="Paid Hoodie", variants=[("Black", "L", "34.99")])
    free = make_product(title="Bible Verse Pack", variants=[("Digital", "", "0.00")])
    make_order(lines=[(paid.variants.get(), 1, "34.99")])
    make_order(lines=[(free.variants.get(), 1, "0.00")], shipping_charged="0.00")

    hidden = client.get("/products/?range=all")
    shown = client.get("/products/?range=all&hide_free=0")

    assert b"Paid Hoodie" in hidden.content
    assert b"Bible Verse Pack" not in hidden.content
    assert b"Bible Verse Pack" in shown.content


def test_orders_page_hides_zero_pound_downloads_by_default(client, make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    paid = make_order(lines=[(product.variants.get(), 1, "34.99")])
    free = make_order(lines=[(product.variants.get(), 1, "0.00")], shipping_charged="0.00")
    free.lines.update(requires_shipping=False)

    hidden = client.get("/orders/")
    shown = client.get("/orders/?hide_free=0")

    assert paid.name.encode() in hidden.content
    assert free.name.encode() not in hidden.content
    assert free.name.encode() in shown.content
    assert b"free download" in shown.content


def test_product_detail_shows_size_mix(client, make_product, map_variant, make_order):
    product = make_product(
        title="Classic Tee",
        variants=[("Black", "S", "24.99"), ("Black", "L", "24.99")],
    )
    small, large = product.variants.order_by("position")
    map_variant(small, cost="8.00")
    map_variant(large, cost="8.00")
    make_order(lines=[(large, 3, "24.99"), (small, 1, "24.99")])

    response = client.get(f"/products/{product.pk}/?range=all")

    assert response.status_code == 200
    assert b"Classic Tee" in response.content
    assert b"<h2 class=\"page-heading\">" in response.content
    assert b"Size mix" in response.content
    assert b">L<" in response.content or b"L" in response.content


def test_blanks_catalogue_lists_inkthreadable_products(client, map_variant, make_product):
    product = make_product(variants=[("Black", "L", "24.99")])
    map_variant(product.variants.get(), cost="8.10")

    listing = client.get("/mapping/blanks/")
    assert listing.status_code == 200
    assert b"Creator 2.0" in listing.content
    assert b"Stanley/Stella" in listing.content
    assert b"AT002" in listing.content
    assert b"AWDis 180" in listing.content

    blank_id = product.variants.get().supplier_mapping.supplier_variant.product_id
    detail = client.get(f"/mapping/blanks/{blank_id}/")
    assert detail.status_code == 200
    assert b"8.10" in detail.content


def test_customers_page_separates_new_and_returning(client, make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    returning = Customer.objects.create(first_name="Ada", last_name="Lovelace", email="ada@example.com")
    newbie = Customer.objects.create(first_name="Grace", last_name="Hopper", email="grace@example.com")
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=returning, when=date(2026, 3, 1))
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=returning, when=date(2026, 6, 15))
    make_order(lines=[(product.variants.get(), 1, "34.99")], customer=newbie, when=date(2026, 6, 20))

    listing = client.get("/customers/?range=all")
    assert listing.status_code == 200
    assert b"Ada Lovelace" in listing.content
    assert b"Grace Hopper" in listing.content
    assert b"returning" in listing.content
    assert b"new" in listing.content

    detail = client.get(f"/customers/{returning.pk}/?range=all")
    assert detail.status_code == 200
    assert b"Ada Lovelace" in detail.content
    assert b"Lifetime paid orders" in detail.content


def test_shipping_page_separates_charged_from_missing_cost(client, make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    paid = make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        shipping_charged="3.95",
        shipping_country="GB",
        name="#3101",
    )
    SupplierOrder.objects.create(
        supplier_reference="IT-ship",
        order=paid,
        product_cost=Decimal("8.00"),
        shipping_cost=Decimal("2.95"),
    )
    make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        shipping_charged="0.00",
        shipping_country="US",
        name="#3102",
    )
    digital = make_order(
        lines=[(product.variants.get(), 1, "0.00")],
        shipping_charged="0.00",
        name="#3103",
    )
    digital.lines.update(requires_shipping=False)

    listing = client.get("/shipping/?range=all")
    assert listing.status_code == 200
    assert b"#3101" in listing.content
    assert b"#3102" in listing.content
    assert b"#3103" not in listing.content
    assert b"sort=cost" in listing.content
    assert b"osort=charged" in listing.content
    assert b"free postage" in listing.content.lower() or b"Free postage" in listing.content

    free_only = client.get("/shipping/?range=all&free=1")
    assert b"#3101" not in free_only.content
    assert b"#3102" in free_only.content


def test_discounts_page_lists_codes_and_sort_headers(client, make_product, make_order):
    product = make_product(variants=[("Black", "L", "34.99")])
    make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        discounts="3.50",
        discount_codes=["WELCOME10"],
        name="#4101",
    )
    make_order(
        lines=[(product.variants.get(), 1, "34.99")],
        discounts="7.00",
        discount_codes=["FESTIVALFITS"],
        name="#4102",
    )

    listing = client.get("/discounts/?range=all")
    assert listing.status_code == 200
    assert b"WELCOME10" in listing.content
    assert b"FESTIVALFITS" in listing.content
    assert b"#4101" in listing.content
    assert b"sort=amount" in listing.content
    assert b"osort=date" in listing.content

    filtered = client.get("/discounts/?range=all&code=WELCOME10")
    assert b"#4101" in filtered.content
    assert b"#4102" not in filtered.content


def test_refunds_page_lists_returned_products(client, make_product, make_order, refund_order):
    quiet = make_product(title="Quiet Tee", variants=[("Black", "L", "20.00")])
    noisy = make_product(title="Noisy Hoodie", variants=[("Black", "L", "20.00")])
    make_order(lines=[(quiet.variants.get(), 1, "20.00")], shipping_charged="0.00", name="#5101")
    back = make_order(lines=[(noisy.variants.get(), 1, "20.00")], shipping_charged="0.00", name="#5102")
    refund_order(back, amount="20.00")

    listing = client.get("/refunds/?range=all")
    assert listing.status_code == 200
    assert b"Noisy Hoodie" in listing.content
    assert b"#5102" in listing.content
    assert b"sort=rate" in listing.content
    assert b"Quiet Tee" not in listing.content

    shown = client.get("/refunds/?range=all&hide_zero=0")
    assert b"Quiet Tee" in shown.content

    filtered = client.get(f"/refunds/?range=all&product={noisy.pk}")
    assert b"#5102" in filtered.content
    assert b"#5101" not in filtered.content


def test_groups_page_ranks_hoodies_and_tees(client, make_product, make_order):
    hoodie = make_product(title="Lifeline Hoodie", variants=[("Black", "L", "34.99")])
    tee = make_product(title="Peace T-Shirt", variants=[("Black", "L", "24.99")])
    make_order(lines=[(hoodie.variants.get(), 2, "34.99")])
    make_order(lines=[(tee.variants.get(), 1, "24.99")], shipping_charged="0.00")

    listing = client.get("/groups/?range=all")
    assert listing.status_code == 200
    assert b"Hoodies" in listing.content
    assert b"T-Shirts" in listing.content
    assert b"sort=units" in listing.content

    hoodies = ProductGroup.objects.get(slug="hoodies")
    detail = client.get(f"/groups/{hoodies.pk}/?range=all")
    assert detail.status_code == 200
    assert b"Lifeline Hoodie" in detail.content
    assert b"Peace T-Shirt" not in detail.content
    assert b"Contribution profit" in detail.content
    assert b"Mapping" in detail.content
    assert b"none of the sold lines have a printer cost" in detail.content


def test_pages_accept_a_custom_date_range(client, make_txn, category_by_name):
    make_txn("40.00", category=category_by_name("Shopify Payout"), when=date(2026, 6, 10))
    response = client.get("/?start=2026-06-01&end=2026-06-30")
    assert response.status_code == 200
    assert b"Apply dates" in response.content
    assert b'value="2026-06-01"' in response.content
    assert b'value="2026-06-30"' in response.content
    assert b"start=2026-06-01" in response.content


def test_product_detail_compares_to_the_previous_window(client, make_product, map_variant, make_order):
    product = make_product(title="Classic Tee", variants=[("Black", "L", "24.99")])
    map_variant(product.variants.get(), cost="8.00")
    make_order(lines=[(product.variants.get(), 1, "24.99")], when=date(2026, 7, 10))
    make_order(lines=[(product.variants.get(), 3, "24.99")], when=date(2026, 8, 10))

    response = client.get(f"/products/{product.pk}/?start=2026-08-01&end=2026-08-31")

    assert response.status_code == 200
    assert b"vs previous" in response.content


def test_guide_renders_the_operator_handbook(client):
    response = client.get("/guide/")
    assert response.status_code == 200
    assert b"How to use this app" in response.content
    assert b"Finance" in response.content
    assert b"Link supplier orders" in response.content


def test_categories_page_lists_and_adds_rules(client, seeded, category_by_name):
    listing = client.get("/categories/")
    assert listing.status_code == 200
    assert b"INKTHREADABLE" in listing.content

    apparel = category_by_name("Apparel")
    added = client.post(
        "/categories/",
        {
            "action": "add",
            "pattern": "NEWPAYEE",
            "match_type": "CONTAINS",
            "match_field": "ANY_TEXT",
            "amount_condition": "ANY",
            "category": str(apparel.pk),
            "priority": "40",
            "name": "New payee",
            "is_active": "on",
        },
    )
    assert added.status_code == 302
    assert CategoryRule.objects.filter(pattern="NEWPAYEE").exists()


def test_data_health_can_relink_without_calling_the_api(client):
    response = client.post("/integrations/", {"action": "relink"})
    assert response.status_code == 302


def test_data_health_is_the_only_place_needed_to_update_the_shop(client):
    response = client.get("/integrations/")
    assert response.status_code == 200
    assert b"Update Shopify" in response.content
    assert b"Update Inkthreadable" in response.content
    assert b"Import bank CSV" in response.content
    assert b"Import spreadsheet" in response.content
    assert b"manage.py" not in response.content


def test_cash_table_can_be_downloaded_as_csv(client, make_txn, category_by_name):
    make_txn("40.00", category=category_by_name("Shopify Payout"), counterparty="Stripe")
    response = client.get("/?range=all&export=csv")
    assert response.status_code == 200
    assert "text/csv" in response["Content-Type"]
    assert b"Revenue" in response.content


def test_guide_has_no_terminal_steps(client):
    response = client.get("/guide/")
    assert response.status_code == 200
    assert b"manage.py" not in response.content
    assert b".venv" not in response.content
    assert b"Change password" in response.content


def test_operator_login_is_created_once():
    from django.contrib.auth import get_user_model

    from apps.web.bootstrap import ensure_operator_user

    User = get_user_model()
    first, created = ensure_operator_user()
    first.set_password("1234")
    first.save()
    second, created_again = ensure_operator_user()
    assert created or User.objects.filter(username="laura").exists()
    assert first.pk == second.pk
    assert not created_again
    assert first.check_password("1234")


def test_change_password_page_is_linked_for_a_signed_in_user(client, django_user_model):
    user = django_user_model.objects.create_user(username="laura", password="1234")
    client.force_login(user)
    home = client.get("/")
    assert home.status_code == 200
    assert b"Change password" in home.content
    form = client.get("/accounts/password_change/")
    assert form.status_code == 200
    assert b"Current password" in form.content
