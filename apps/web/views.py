"""Views for the working interface.

Business calculations stay in ``apps.analytics``. These views only load the
results and render them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe

from apps.analytics.cashflow import (
    Mode,
    compare_periods,
    compute_cash_metrics,
    monthly_series,
)
from apps.analytics.customers import compute_customer_metrics, monthly_customer_mix, rank_customers
from apps.analytics.groups import ensure_default_groups, ungrouped_products
from apps.analytics.discounts import (
    compute_discounts,
    monthly_discounts,
    rank_codes,
    rank_discount_orders,
)
from apps.analytics.refunds import (
    compute_refunds,
    filter_refund_items,
    monthly_refunds,
    rank_refund_events,
    rank_refund_items,
)
from apps.analytics.insights import build_insights
from apps.analytics.shipping import (
    compute_shipping,
    monthly_shipping,
    rank_countries,
    rank_shipping_orders,
)
from apps.analytics.profitability import (
    CostResolver,
    Performance,
    all_group_performance,
    all_product_performance,
    compare_performance,
    compute_order_profit,
    compute_order_profits,
    filter_product_rows,
    group_performance,
    option_mix,
    product_performance,
    rank_products,
    summarise,
    trading_by_month,
    variant_performance,
)
from apps.catalog.models import Product, ProductGroup
from apps.core.money import ZERO, fmt
from apps.core.periods import PRESETS, requested_range
from apps.finance.categorisation import categorise
from apps.finance.importers import import_finance_dashboard, import_monzo_csv
from apps.finance.models import BankAccount, BankTransaction, CashLink, ImportBatch, TransactionSource
from apps.finance.seed import seed_all
from apps.integrations.inkthreadable.sync import relink_supplier_orders, sync_orders as sync_inkthreadable_orders
from apps.integrations.jobs import is_sync_running, start_job
from apps.integrations.models import SyncRun, SyncService
from apps.integrations.shopify.sync import sync_all as sync_shopify_all
from apps.analytics.reconciliation import apply_suggestions as apply_reconciliation_suggestions
from apps.supplier.mapping import apply_suggestions as apply_mapping_suggestions
from apps.web.bootstrap import ensure_operator_user
from apps.web.exports import csv_response, money_cell, wants_csv
from apps.sales.models import Customer, Order
from apps.supplier.models import ProductMapping, SupplierOrder, SupplierProduct, VariantMapping
from apps.web.charts import column_chart, line_chart, share_bars


def healthz(request):
    """Liveness probe for Render."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok", "database": "ok"})
    except Exception as exc:  # noqa: BLE001 - the response is the report
        return JsonResponse({"status": "degraded", "database": str(exc)}, status=503)


def dashboard(request):
    """Cash dashboard from bank transactions — the Stage 1 view."""
    include_drawings = _flag(request, "drawings", default=False)
    sort = request.GET.get("sort", "total")
    if sort not in {"name", "kind", "count", "total", "share"}:
        sort = "total"

    earliest = BankTransaction.objects.order_by("occurred_on").values_list("occurred_on", flat=True).first()
    date_range, preset = requested_range(request.GET, earliest=earliest)

    metrics = compute_cash_metrics(date_range, mode=Mode.STANDARD)
    comparison = compare_periods(date_range, against="last_year", mode=Mode.STANDARD)
    month_rows = [
        {
            "label": row["label"],
            "revenue": row["revenue"],
            "profit": row["profit"] if include_drawings else row["profit_excluding_distributions"],
            "expenses": row["expenses"] if include_drawings else row["expenses_excluding_distributions"],
        }
        for row in monthly_series(date_range, mode=Mode.STANDARD)
    ]
    months = column_chart(month_rows, ["revenue", "profit", "expenses"])
    profit_line = line_chart(month_rows, ["profit", "revenue"])

    rows = [
        ("Revenue", metrics.revenue),
        ("Cost of goods sold", metrics.cost_of_goods),
        ("Shipping", metrics.shipping_costs),
        ("Payment fees", metrics.payment_fees),
        ("Contribution profit", metrics.contribution_profit),
        ("Operating expenses", metrics.operating_expenses),
        ("Operating profit", metrics.operating_profit),
        ("Expenses (exc. salary & tithe)", metrics.total_expenses_excluding_distributions),
        ("Profit (exc. salary & tithe)", metrics.net_profit_excluding_distributions),
        ("Owner distributions", metrics.distributions),
        ("Net profit", metrics.net_profit),
    ]

    displayed_profit = metrics.net_profit if include_drawings else metrics.net_profit_excluding_distributions
    displayed_expenses = (
        metrics.total_expenses if include_drawings else metrics.total_expenses_excluding_distributions
    )
    displayed_margin = (
        metrics.net_margin_pct if include_drawings else metrics.net_margin_excluding_distributions_pct
    )
    profit_change = comparison.change("net_profit" if include_drawings else "net_profit_excluding_distributions")
    extras = []
    if include_drawings:
        extras.append("drawings=1")
    if sort != "total":
        extras.append(f"sort={sort}")

    if wants_csv(request):
        return csv_response(
            "cash.csv",
            ["Line", "Amount"],
            [[label, money_cell(value)] for label, value in rows]
            + [[]]
            + [["Category", "Kind", "Transactions", "Total"]]
            + [
                [row["name"], row["kind"], row["count"], money_cell(row["total"])]
                for row in metrics.by_category
            ],
        )

    categories = list(metrics.by_category)
    if sort == "name":
        categories.sort(key=lambda row: row["name"].casefold())
    elif sort == "kind":
        categories.sort(key=lambda row: (row["kind"], row["name"].casefold()))
    elif sort == "count":
        categories.sort(key=lambda row: (-row["count"], row["name"].casefold()))
    elif sort == "share":
        categories.sort(key=lambda row: (-(row["pct_of_total"] or 0), row["name"].casefold()))
    else:
        categories.sort(key=lambda row: (-row["magnitude"], row["name"].casefold()))

    return render(
        request,
        "web/dashboard.html",
        {
            "nav": "cash",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "extra_query": "&".join(extras),
            "include_drawings": include_drawings,
            "sort": sort,
            "metrics": metrics,
            "displayed_profit": displayed_profit,
            "displayed_expenses": displayed_expenses,
            "displayed_margin": displayed_margin,
            "year_ago": {
                "revenue_pct": comparison.change("revenue"),
                "profit_pct": profit_change,
            },
            "months": months,
            "profit_line": profit_line,
            "rows": [(label, fmt(value)) for label, value in rows],
            "contribution_margin": metrics.contribution_margin_pct,
            "net_margin": displayed_margin,
            "categories": categories,
        },
    )


def insights(request):
    """Numbered findings from figures the rest of the app already computed."""
    date_range, preset = _requested_range(request)
    findings = build_insights(date_range, range_key=preset)
    if wants_csv(request):
        return csv_response(
            "insights.csv",
            ["Number", "Title", "Finding"],
            [[index, item.title, item.body] for index, item in enumerate(findings, start=1)],
        )
    return render(
        request,
        "web/insights.html",
        {
            "nav": "insights",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "insights": findings,
        },
    )


def integrations(request):
    """Connection and sync status for every data source."""
    seed_all()
    ensure_operator_user()
    if request.method == "POST":
        return _integrations_post(request)

    shopify_products = SyncRun.objects.filter(service=SyncService.SHOPIFY, resource="products").first()
    shopify_orders = SyncRun.objects.filter(service=SyncService.SHOPIFY, resource="orders").first()
    ink = SyncRun.objects.filter(service=SyncService.INKTHREADABLE).first()
    last_monzo = (
        ImportBatch.objects.filter(source=TransactionSource.MONZO_CSV).order_by("-created_at").first()
        or ImportBatch.objects.order_by("-created_at").first()
    )

    sources = [
        {
            "name": "Shopify",
            "ok": bool(shopify_orders and shopify_orders.status == "success"),
            "last": shopify_orders.finished_at if shopify_orders else None,
            "detail": (
                f"{Product.objects.count()} products, {Order.objects.count()} orders"
                if Product.objects.exists() or Order.objects.exists()
                else "No records synced yet"
            ),
            "warning": _sync_warning(shopify_products, shopify_orders),
        },
        {
            "name": "Inkthreadable",
            "ok": bool(ink and ink.status == "success"),
            "last": ink.finished_at if ink else None,
            "detail": (
                f"{SupplierOrder.objects.count()} supplier orders, "
                f"{SupplierOrder.objects.filter(order__isnull=False).count()} linked to Shopify"
            ),
            "warning": _sync_warning(ink)
            or (
                f"{SupplierProduct.objects.filter(is_discontinued=True).count()} blank(s) marked phased out"
                if SupplierProduct.objects.filter(is_discontinued=True).exists()
                else ""
            ),
        },
        {
            "name": "Monzo / bank",
            "ok": BankTransaction.objects.exists(),
            "last": last_monzo.created_at if last_monzo else None,
            "detail": f"{BankTransaction.objects.count()} transactions",
            "warning": (
                f"{BankTransaction.objects.filter(category__isnull=True).count()} uncategorised"
                if BankTransaction.objects.filter(category__isnull=True).exists()
                else "Matches transactions.csv + historic_transactions.csv (1 known Monzo duplicate skipped)"
            ),
        },
        {
            "name": "Product mapping",
            "ok": VariantMapping.objects.exists() or ProductMapping.objects.filter(no_supplier=True).exists(),
            "last": None,
            "detail": (
                f"{VariantMapping.objects.count()} variants mapped, "
                f"{ProductMapping.objects.filter(no_supplier=True).count()} digital products"
            ),
            "warning": (
                "Review matches on the Mapping page"
                if Product.objects.exists() and not VariantMapping.objects.filter(confidence="confirmed").exists()
                else ""
            ),
        },
        {
            "name": "Reconciliation",
            "ok": CashLink.objects.filter(confidence="confirmed").exists(),
            "last": CashLink.objects.order_by("-updated_at").values_list("updated_at", flat=True).first(),
            "detail": (
                f"{CashLink.objects.filter(confidence='confirmed').count()} confirmed, "
                f"{CashLink.objects.filter(confidence='suggested').count()} suggested"
            ),
            "warning": "Open Reconciliation to match Shopify days to Stripe and invoices to Inkthreadable",
        },
    ]
    return render(
        request,
        "web/integrations.html",
        {
            "nav": "integrations",
            "sources": sources,
            "sync_running": is_sync_running(),
            "needs_bank": not BankTransaction.objects.exists(),
            "needs_shop": not Order.objects.exists(),
        },
    )


def _integrations_post(request):
    action = request.POST.get("action")
    if action == "relink":
        result = relink_supplier_orders()
        messages.success(
            request,
            (
                f"Linked {result['linked']} Inkthreadable order(s) to Shopify. "
                f"{result['already']} already linked, {result['unmatched']} left unmatched "
                f"(Wix, Etsy and website jobs stay unmatched — not guessed)."
            ),
        )
        return redirect("web:integrations")
    if action == "suggest_mappings":
        result = apply_mapping_suggestions()
        messages.success(request, f"Mapping suggestions updated: {result}")
        return redirect("web:integrations")
    if action == "suggest_reconciliation":
        result = apply_reconciliation_suggestions()
        messages.success(
            request,
            (
                f"Bank matches suggested: {result.get('created', 0)} new, "
                f"{result.get('updated', 0)} updated. Confirmed links were left alone. "
                f"Review them on Reconciliation."
            ),
        )
        return redirect("web:integrations")
    if action == "sync_shopify":
        if start_job("shopify", sync_shopify_all):
            messages.success(request, "Shopify update started. This page will refresh until it finishes.")
        else:
            messages.error(request, "An update is already running. Wait for it to finish, then try again.")
        return redirect("web:integrations")
    if action == "sync_inkthreadable":
        if start_job("inkthreadable", sync_inkthreadable_orders):
            messages.success(
                request,
                "Inkthreadable update started. The first pull can take a long time — leave this page open.",
            )
        else:
            messages.error(request, "An update is already running. Wait for it to finish, then try again.")
        return redirect("web:integrations")
    if request.FILES.get("workbook"):
        return _import_workbook_upload(request)
    if request.FILES.get("monzo_csv"):
        return _import_monzo_upload(request)
    return redirect("web:integrations")


def _import_monzo_upload(request):
    """Accept a Monzo CSV. Overlapping months are deduped by transaction ID."""
    upload = request.FILES["monzo_csv"]
    if not upload.name.lower().endswith(".csv"):
        messages.error(request, "Please upload a Monzo CSV export.")
        return redirect("web:integrations")

    account, _ = BankAccount.objects.get_or_create(
        name="Monzo Business",
        defaults={"institution": "Monzo", "currency": "GBP", "is_primary": True},
    )
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp_path = Path(tmp.name)
    try:
        report = import_monzo_csv(tmp_path, account=account)
    finally:
        tmp_path.unlink(missing_ok=True)

    if report.created:
        categorise(
            BankTransaction.objects.filter(import_batch_id=report.batch_id),
            only_uncategorised=True,
        )
    messages.success(
        request,
        (
            f"{upload.name}: {report.created} new, {report.duplicates} already held "
            f"(left untouched, including any category you set by hand)."
        ),
    )
    if report.issues:
        messages.error(request, f"{len(report.issues)} row(s) needed attention.")
    return redirect("web:integrations")


def _import_workbook_upload(request):
    """Read the Finance Dashboard workbook. The file is never written back."""
    upload = request.FILES["workbook"]
    name = upload.name.lower()
    if not name.endswith(".xlsx"):
        messages.error(request, "Please upload the Finance Dashboard Excel file (.xlsx).")
        return redirect("web:integrations")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp_path = Path(tmp.name)
    try:
        reports = import_finance_dashboard(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    created = sum(report.created for report in reports)
    duplicates = sum(report.duplicates for report in reports)
    messages.success(
        request,
        f"{upload.name}: {created} new rows, {duplicates} already held. The spreadsheet itself was not changed.",
    )
    return redirect("web:integrations")


def _sync_warning(*runs) -> str:
    for run in runs:
        if run is None:
            return "Not synced yet"
        if run.status == "failed":
            return run.error or "Last sync failed"
        if run.records_failed:
            return f"{run.records_failed} record(s) failed on last sync"
    return ""


def products(request):
    """Which products sold, and whether they actually made money."""
    date_range, preset = _requested_range(request)
    sort = request.GET.get("sort", "units")
    if sort not in {"name", "units", "orders", "revenue", "profit", "margin"}:
        sort = "units"
    hide_free = _flag(request, "hide_free", default=True)
    hide_zero = _flag(request, "hide_zero", default=True)

    profits = compute_order_profits(date_range)
    shop = summarise(profits, label="Shop", date_range=date_range)
    sold = all_product_performance(profits, date_range=date_range)
    if not hide_zero:
        sold_ids = {row.product_id for row in sold if row.product_id}
        sold.extend(
            Performance(label=product.title, product_id=product.pk, date_range=date_range)
            for product in Product.objects.exclude(pk__in=sold_ids).only("id", "title")
        )
    ranked = filter_product_rows(
        rank_products(sold, sort=sort),
        hide_free=hide_free,
        hide_zero_orders=hide_zero,
    )
    months = column_chart(
        [
            {"label": row.label, "revenue": row.net_revenue, "profit": row.profit, "units": row.units}
            for row in trading_by_month(profits, date_range)
        ],
        ["revenue", "profit"],
    )
    top_units = share_bars(
        [{"label": row.label, "units": row.units, "product_id": row.product_id} for row in ranked],
        value_key="units",
        limit=10,
    )
    top_revenue = share_bars(
        [
            {"label": row.label, "revenue": row.net_revenue, "product_id": row.product_id}
            for row in ranked
            if not row.is_free
        ],
        value_key="revenue",
        limit=10,
    )
    extras = f"sort={sort}"
    if not hide_free:
        extras += "&hide_free=0"
    if not hide_zero:
        extras += "&hide_zero=0"
    if wants_csv(request):
        return csv_response(
            "products.csv",
            ["Product", "Orders", "Units", "Net sales", "Profit", "Margin %", "Cost coverage"],
            [
                [
                    row.label,
                    row.orders,
                    row.units,
                    money_cell(row.net_revenue),
                    money_cell(row.profit) if row.is_complete else "",
                    row.margin_pct if row.is_complete else "",
                    row.completeness_pct,
                ]
                for row in ranked
            ],
        )
    return render(
        request,
        "web/products.html",
        {
            "nav": "products",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "sort": sort,
            "hide_free": hide_free,
            "hide_zero": hide_zero,
            "shop": shop,
            "products": ranked,
            "months": months,
            "top_units": top_units,
            "top_revenue": top_revenue,
            "free_count": sum(1 for row in sold if row.is_free),
            "extra_query": extras,
        },
    )


def product_detail(request, pk: int):
    product = get_object_or_404(
        Product.objects.select_related("supplier_product_mapping").prefetch_related("variants"),
        pk=pk,
    )
    date_range, preset = _requested_range(request)
    profits = compute_order_profits(date_range)
    performance = product_performance(profits, product, date_range=date_range)
    months = column_chart(
        [
            {"label": row.label, "revenue": row.net_revenue, "profit": row.profit}
            for row in trading_by_month(
                profits,
                date_range,
                line_filter=lambda lp, product_id=product.pk: lp.line.product_id == product_id,
            )
        ],
        ["revenue", "profit"],
    )
    sizes = option_mix(profits, product, attribute="size")
    colours = option_mix(profits, product, attribute="colour")
    previous_range = date_range.previous_period()
    previous = product_performance(
        compute_order_profits(previous_range),
        product,
        date_range=previous_range,
    )
    return render(
        request,
        "web/product_detail.html",
        {
            "nav": "products",
            "product": product,
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "performance": performance,
            "vs_previous": compare_performance(performance, previous),
            "months": months,
            "variants": variant_performance(profits, product=product, date_range=date_range),
            "sizes": share_bars(sizes, value_key="units", limit=12),
            "colours": share_bars(colours, value_key="units", limit=12),
            "mapping": getattr(product, "supplier_product_mapping", None),
        },
    )


def groups(request):
    """Hoodies, tees and the rest — Shopify types are empty, so titles fill the groups."""
    ensure_default_groups()
    date_range, preset = _requested_range(request)
    sort = request.GET.get("sort", "revenue")
    if sort not in {"name", "units", "orders", "revenue", "profit", "margin"}:
        sort = "revenue"
    hide_free = _flag(request, "hide_free", default=True)

    profits = compute_order_profits(date_range)
    group_rows = all_group_performance(
        profits,
        ProductGroup.objects.prefetch_related("products"),
        date_range=date_range,
    )
    members = {group.pk: group.products.count() for group in ProductGroup.objects.all()}
    for row in group_rows:
        row.members = members.get(row.group_id, 0)
    ranked = rank_products(group_rows, sort=sort)
    if hide_free:
        ranked = [row for row in ranked if not row.is_free]
    extras = f"sort={sort}"
    if not hide_free:
        extras += "&hide_free=0"
    if wants_csv(request):
        return csv_response(
            "groups.csv",
            ["Group", "Orders", "Units", "Net sales", "Profit", "Margin %"],
            [
                [
                    row.label,
                    row.orders,
                    row.units,
                    money_cell(row.net_revenue),
                    money_cell(row.profit) if row.is_complete else "",
                    row.margin_pct if row.is_complete else "",
                ]
                for row in ranked
            ],
        )
    return render(
        request,
        "web/groups.html",
        {
            "nav": "groups",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "sort": sort,
            "hide_free": hide_free,
            "groups": ranked,
            "ungrouped": ungrouped_products().count(),
            "extra_query": extras,
        },
    )


def group_detail(request, pk: int):
    ensure_default_groups()
    group = get_object_or_404(ProductGroup.objects.prefetch_related("products"), pk=pk)
    date_range, preset = _requested_range(request)
    sort = request.GET.get("sort", "units")
    if sort not in {"name", "units", "orders", "revenue", "profit", "margin"}:
        sort = "units"
    hide_free = _flag(request, "hide_free", default=True)
    hide_zero = _flag(request, "hide_zero", default=True)

    profits = compute_order_profits(date_range)
    performance = group_performance(profits, group, date_range=date_range)
    member_ids = set(group.products.values_list("pk", flat=True))
    sold = [
        row
        for row in all_product_performance(profits, date_range=date_range)
        if row.product_id in member_ids
    ]
    if not hide_zero:
        sold_ids = {row.product_id for row in sold if row.product_id}
        sold.extend(
            Performance(label=product.title, product_id=product.pk, date_range=date_range)
            for product in group.products.exclude(pk__in=sold_ids).only("id", "title")
        )
    ranked = filter_product_rows(
        rank_products(sold, sort=sort),
        hide_free=hide_free,
        hide_zero_orders=hide_zero,
    )
    extras = f"sort={sort}"
    if not hide_free:
        extras += "&hide_free=0"
    if not hide_zero:
        extras += "&hide_zero=0"
    return render(
        request,
        "web/group_detail.html",
        {
            "nav": "groups",
            "group": group,
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "sort": sort,
            "hide_free": hide_free,
            "hide_zero": hide_zero,
            "performance": performance,
            "products": ranked,
            "extra_query": extras,
        },
    )


def _requested_range(request):
    earliest_order = (
        Order.objects.countable().order_by("placed_on").values_list("placed_on", flat=True).first()
    )
    earliest_bank = BankTransaction.objects.order_by("occurred_on").values_list("occurred_on", flat=True).first()
    candidates = [day for day in (earliest_order, earliest_bank) if day]
    earliest = min(candidates) if candidates else None
    return requested_range(request.GET, earliest=earliest)


def _flag(request, name: str, *, default: bool = True) -> bool:
    raw = request.GET.get(name)
    if raw is None:
        return default
    return raw not in {"0", "false", "off", ""}


def customers(request):
    """Who bought, who is new, and what they spent. Free downloads excluded by default."""
    date_range, preset = _requested_range(request)
    hide_free = _flag(request, "hide_free", default=True)
    sort = request.GET.get("sort", "spend")
    if sort not in {"spend", "orders", "last", "first", "name"}:
        sort = "spend"

    metrics = compute_customer_metrics(date_range, paid_only=hide_free)
    rows = rank_customers(metrics.rows, sort=sort)
    months = column_chart(monthly_customer_mix(metrics, date_range), ["new", "returning"])
    if wants_csv(request):
        return csv_response(
            "customers.csv",
            ["Name", "Email", "Country", "New", "First paid", "Last paid", "Period orders", "Period spend"],
            [
                [
                    row.name,
                    row.email,
                    row.country,
                    "yes" if row.is_new else "no",
                    row.first_paid_on,
                    row.last_paid_on,
                    row.period_orders,
                    money_cell(row.period_spend),
                ]
                for row in rows
            ],
        )
    return render(
        request,
        "web/customers.html",
        {
            "nav": "customers",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "hide_free": hide_free,
            "sort": sort,
            "metrics": metrics,
            "customers": rows,
            "months": months,
            "extra_query": "" if hide_free else "hide_free=0",
        },
    )


def customer_detail(request, pk: int):
    customer = get_object_or_404(Customer, pk=pk)
    date_range, preset = _requested_range(request)
    hide_free = _flag(request, "hide_free", default=True)
    qs = customer.orders.countable().prefetch_related("lines")
    if hide_free:
        qs = qs.exclude(total_price=0)
    history = list(qs.order_by("placed_on"))
    period_orders = [order for order in history if date_range.contains(order.placed_on)]
    lifetime_spend = sum((order.total_price or ZERO) for order in history) or ZERO
    period_spend = sum((order.total_price or ZERO) for order in period_orders) or ZERO
    first = history[0] if history else None
    return render(
        request,
        "web/customer_detail.html",
        {
            "nav": "customers",
            "customer": customer,
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "hide_free": hide_free,
            "orders": list(reversed(history[-50:])),
            "lifetime_orders": len(history),
            "lifetime_spend": lifetime_spend,
            "period_orders": len(period_orders),
            "period_spend": period_spend,
            "first_order": first,
            "is_new": bool(first and date_range.contains(first.placed_on)),
            "extra_query": "" if hide_free else "hide_free=0",
        },
    )


def shipping(request):
    """Postage the customer paid versus Inkthreadable cost. Missing cost stays —."""
    date_range, preset = _requested_range(request)
    sort = request.GET.get("sort", "charged")
    if sort not in {"country", "orders", "charged", "cost", "net", "free"}:
        sort = "charged"
    osort = request.GET.get("osort", "date")
    if osort not in {"date", "name", "country", "charged", "cost", "net"}:
        osort = "date"
    free_only = request.GET.get("free") in {"1", "on", "true", "yes"}

    metrics = compute_shipping(date_range)
    order_rows = [row for row in metrics.rows if row.is_free] if free_only else list(metrics.rows)
    months = column_chart(monthly_shipping(metrics, date_range), ["charged", "cost"])
    extra = f"sort={sort}&osort={osort}"
    if free_only:
        extra += "&free=1"
    if wants_csv(request):
        return csv_response(
            "shipping.csv",
            ["Order", "Date", "Country", "Charged", "Supplier cost", "Free postage"],
            [
                [
                    row.name,
                    row.placed_on,
                    row.country,
                    money_cell(row.charged),
                    money_cell(row.cost),
                    "yes" if row.is_free else "no",
                ]
                for row in rank_shipping_orders(order_rows, sort=osort)
            ],
        )
    return render(
        request,
        "web/shipping.html",
        {
            "nav": "shipping",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "sort": sort,
            "osort": osort,
            "free_only": free_only,
            "metrics": metrics,
            "countries": rank_countries(metrics.countries, sort=sort),
            "orders": rank_shipping_orders(order_rows, sort=osort),
            "months": months,
            "extra_query": extra,
        },
    )


def discounts(request):
    """Codes and how much they gave away. Profit impact stays — without a cost."""
    date_range, preset = _requested_range(request)
    sort = request.GET.get("sort", "amount")
    if sort not in {"code", "orders", "amount", "avg", "profit"}:
        sort = "amount"
    osort = request.GET.get("osort", "date")
    if osort not in {"date", "name", "code", "amount", "total", "profit"}:
        osort = "date"
    hide_digital = _flag(request, "hide_digital", default=True)
    code = (request.GET.get("code") or "").strip()

    metrics = compute_discounts(date_range, hide_digital=hide_digital)
    order_rows = [row for row in metrics.rows if row.code == code] if code else list(metrics.rows)
    extra = f"sort={sort}&osort={osort}"
    if not hide_digital:
        extra += "&hide_digital=0"
    if code:
        extra += f"&code={quote(code)}"
    if wants_csv(request):
        return csv_response(
            "discounts.csv",
            ["Code", "Orders", "Amount given away"],
            [
                [row.code, row.orders, money_cell(row.amount)]
                for row in rank_codes(metrics.codes, sort=sort)
            ],
        )
    return render(
        request,
        "web/discounts.html",
        {
            "nav": "discounts",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "sort": sort,
            "osort": osort,
            "hide_digital": hide_digital,
            "code": code,
            "metrics": metrics,
            "codes": rank_codes(metrics.codes, sort=sort),
            "orders": rank_discount_orders(order_rows, sort=osort),
            "months": column_chart(monthly_discounts(metrics, date_range), ["amount"]),
            "extra_query": extra,
        },
    )


def refunds(request):
    """Of what sold in the period, how much came back. Line amounts are not extra cash."""
    date_range, preset = _requested_range(request)
    sort = request.GET.get("sort", "rate")
    if sort not in {"rate", "name", "units", "refunded", "amount"}:
        sort = "rate"
    vsort = request.GET.get("vsort", "rate")
    if vsort not in {"rate", "name", "units", "refunded", "amount"}:
        vsort = "rate"
    osort = request.GET.get("osort", "date")
    if osort not in {"date", "placed", "name", "amount", "units"}:
        osort = "date"
    hide_digital = _flag(request, "hide_digital", default=True)
    hide_zero = _flag(request, "hide_zero", default=True)
    product_id = request.GET.get("product")
    try:
        product_id = int(product_id) if product_id else None
    except (TypeError, ValueError):
        product_id = None

    metrics = compute_refunds(date_range, hide_digital=hide_digital)
    products = filter_refund_items(metrics.products, hide_zero=hide_zero)
    variants = filter_refund_items(metrics.variants, hide_zero=hide_zero)
    events = list(metrics.rows)
    product_label = ""
    if product_id is not None:
        variants = [row for row in variants if row.product_id == product_id]
        events = [row for row in events if product_id in row.product_ids]
        match = next((row for row in metrics.products if row.product_id == product_id), None)
        product_label = match.label if match else f"Product {product_id}"
    extra = f"sort={sort}&vsort={vsort}&osort={osort}"
    if not hide_digital:
        extra += "&hide_digital=0"
    if not hide_zero:
        extra += "&hide_zero=0"
    if product_id is not None:
        extra += f"&product={product_id}"
    if wants_csv(request):
        return csv_response(
            "refunds.csv",
            ["Product", "Units sold", "Units returned", "Rate %", "Refund cash"],
            [
                [
                    row.label,
                    row.sold_units,
                    row.refunded_units,
                    row.rate_pct,
                    money_cell(row.amount),
                ]
                for row in rank_refund_items(products, sort=sort)
            ],
        )
    return render(
        request,
        "web/refunds.html",
        {
            "nav": "refunds",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "sort": sort,
            "vsort": vsort,
            "osort": osort,
            "hide_digital": hide_digital,
            "hide_zero": hide_zero,
            "product_id": product_id,
            "product_label": product_label,
            "metrics": metrics,
            "products": rank_refund_items(products, sort=sort),
            "variants": rank_refund_items(variants, sort=vsort),
            "refunds": rank_refund_events(events, sort=osort),
            "months": column_chart(monthly_refunds(metrics, date_range), ["amount"]),
            "extra_query": extra,
        },
    )


def orders(request):
    hide_free = _flag(request, "hide_free", default=True)
    sort = request.GET.get("sort", "-placed_on")
    allowed = {"placed_on", "-placed_on", "total_price", "-total_price", "name", "-name"}
    if sort not in allowed:
        sort = "-placed_on"

    qs = Order.objects.countable().select_related("customer").prefetch_related(
        "lines", "supplier_orders", "refunds"
    )
    free_count = qs.filter(total_price=0).count()
    if hide_free:
        qs = qs.exclude(total_price=0)
    rows = list(qs.order_by(sort)[:200])
    resolver = CostResolver()
    for order in rows:
        order.is_free = order.total_price == 0
        order.is_digital = order.is_free and not any(line.requires_shipping for line in order.lines.all())
        profit = compute_order_profit(order, resolver=resolver)
        order.contribution_profit = profit.contribution_profit
        order.profit_complete = profit.is_complete
    if wants_csv(request):
        return csv_response(
            "orders.csv",
            ["Order", "Date", "Customer", "Status", "Total", "Profit"],
            [
                [
                    order.name,
                    order.placed_on,
                    order.customer or order.email or "",
                    f"{order.financial_status} / {order.fulfilment_status}",
                    money_cell(order.total_price),
                    money_cell(order.contribution_profit) if order.profit_complete else "",
                ]
                for order in rows
            ],
        )
    return render(
        request,
        "web/orders.html",
        {
            "nav": "orders",
            "orders": rows,
            "total": Order.objects.countable().count(),
            "shown": len(rows),
            "free_count": free_count,
            "hide_free": hide_free,
            "sort": sort,
        },
    )


def order_detail(request, pk: int):
    order = get_object_or_404(
        Order.objects.select_related("customer").prefetch_related(
            "lines", "lines__variant", "refunds", "supplier_orders"
        ),
        pk=pk,
    )
    profit = compute_order_profit(order)
    return render(
        request,
        "web/order_detail.html",
        {"nav": "orders", "order": order, "profit": profit},
    )


def guide(request):
    """Rendered operator guide. The markdown file is the source of truth."""
    import markdown

    path = Path(settings.BASE_DIR) / "docs" / "USER_GUIDE.md"
    source = path.read_text(encoding="utf-8") if path.is_file() else (
        "# How to use this app\n\nThe guide file is missing from this install."
    )
    html = markdown.markdown(
        source,
        extensions=["nl2br", "sane_lists", "tables", "fenced_code"],
    )
    return render(request, "web/guide.html", {"nav": "guide", "guide_html": mark_safe(html)})
