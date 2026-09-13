"""Numbered business insights from figures the rest of the app already computes.

Every line is backed by a calculated number. Nothing is invented, and shop
profit is withheld when costs are missing. Insights that cannot be supported
by the data for the period are omitted rather than padded.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.urls import reverse

from apps.analytics.cashflow import Mode, compute_cash_metrics
from apps.analytics.discounts import compute_discounts, rank_codes
from apps.analytics.refunds import compute_refunds, rank_refund_items
from apps.analytics.shipping import compute_shipping, rank_countries
from apps.analytics.groups import ensure_default_groups
from apps.analytics.profitability import (
    all_group_performance,
    all_product_performance,
    compute_order_profits,
    filter_product_rows,
    rank_products,
    summarise,
)
from apps.catalog.models import ProductGroup
from apps.analytics.reconciliation import is_payout, is_supplier_charge
from apps.core.constants import SHOPIFY_LAUNCH_DATE
from apps.core.money import ZERO, fmt, quantise, safe_divide
from apps.core.periods import DateRange
from apps.finance.models import BankTransaction, CashLink, CategoryKind
from apps.sales.models import Order


@dataclass
class Insight:
    """One numbered finding. ``body`` already contains the formatted amounts."""

    title: str
    body: str
    tone: str = "fact"
    href: str | None = None
    href_label: str | None = None


def build_insights(date_range: DateRange, *, range_key: str = "ytd") -> list[Insight]:
    """Insights that can be stated from the books for ``date_range``."""
    cash = compute_cash_metrics(date_range, mode=Mode.STANDARD)
    profits = compute_order_profits(date_range)
    shop = summarise(profits, label="Shop", date_range=date_range)
    paid_products = filter_product_rows(
        rank_products(all_product_performance(profits, date_range=date_range), sort="units"),
        hide_free=True,
        hide_zero_orders=True,
    )
    paid_orders = list(
        Order.objects.countable()
        .in_range(date_range)
        .filter(total_price__gt=0)
        .select_related("customer")
    )
    free_orders = Order.objects.countable().in_range(date_range).filter(total_price=0).count()
    bank = list(
        BankTransaction.objects.in_range(date_range)
        .exclude(category__kind__in=CategoryKind.outside_pnl())
        .select_related("category")
    )

    rows: list[Insight] = []
    _net_profit(rows, cash, range_key)
    _shopify_vs_bank(rows, shop, cash, date_range, range_key)
    _paid_orders(rows, paid_orders, free_orders, range_key)
    _shipping(rows, date_range, range_key)
    _discounts(rows, date_range, range_key)
    _refunds(rows, date_range, range_key)
    _top_product(rows, paid_products, range_key)
    _concentration(rows, paid_products)
    _cost_coverage(rows, shop, range_key)
    _wix_era(rows, date_range, range_key)
    _cash_counterparties(rows, bank, range_key)
    _repeat_customers(rows, paid_orders, range_key)
    _payout_links(rows, bank, date_range, range_key)
    _groups(rows, profits, date_range, range_key)
    _loss_makers(rows, paid_products, range_key)
    _uncategorised(rows, cash, range_key)
    _card_fees(rows, cash, shop)
    _countries(rows, date_range, range_key)
    return rows


def _qs(range_key: str, **extra) -> str:
    parts = [f"range={range_key}"]
    parts.extend(f"{key}={value}" for key, value in extra.items() if value is not None)
    return "?" + "&".join(parts)


def _net_profit(rows, cash, range_key):
    rows.append(
        Insight(
            title="Cash profit is from Monzo, not Shopify",
            body=(
                f"Bank revenue is {fmt(cash.revenue)} across {cash.transaction_count} transactions. "
                f"Profit excluding salary and tithe is {fmt(cash.net_profit_excluding_distributions)}; "
                f"after those drawings it is {fmt(cash.net_profit)}."
            ),
            href="/" + _qs(range_key),
            href_label="Open Cash",
        )
    )


def _shopify_vs_bank(rows, shop, cash, date_range, range_key):
    if not shop.orders:
        return
    extra = ""
    if date_range.start < SHOPIFY_LAUNCH_DATE:
        extra = (
            f" This range includes days before {SHOPIFY_LAUNCH_DATE:%d %b %Y}, "
            f"when the storefront was on Wix — those bank rows will not have Shopify orders."
        )
    rows.append(
        Insight(
            title="Shopify sales and bank cash are different clocks",
            body=(
                f"Shopify recorded {shop.orders} countable orders and {fmt(shop.net_revenue)} "
                f"net sales (after discounts and refunds). The bank shows {fmt(cash.revenue)} "
                f"revenue in the same dates. Payouts land days later, net of fees."
                f"{extra}"
            ),
            href="/reconciliation/" + _qs(range_key),
            href_label="Open Reconciliation",
        )
    )


def _paid_orders(rows, paid_orders, free_orders, range_key):
    if not paid_orders and not free_orders:
        return
    total = sum((order.total_price or ZERO) for order in paid_orders)
    aov = safe_divide(total, Decimal(len(paid_orders))) if paid_orders else None
    body = (
        f"{len(paid_orders)} paid order{'s' if len(paid_orders) != 1 else ''} "
        f"totalling {fmt(total)}"
    )
    if aov is not None:
        body += f", average {fmt(aov)} per paid order"
    body += "."
    if free_orders:
        body += (
            f" {free_orders} further order{'s' if free_orders != 1 else ''} are £0 "
            f"digital downloads — Shopify charged nothing, so they are not missing prices."
        )
    rows.append(
        Insight(
            title="Paid orders versus free downloads",
            body=body,
            href="/orders/",
            href_label="Open Orders",
        )
    )


def _shipping(rows, date_range, range_key):
    metrics = compute_shipping(date_range)
    if not metrics.shippable:
        return
    body = (
        f"Customers paid {fmt(metrics.charged)} postage on {metrics.shippable} physical "
        f"order{'s' if metrics.shippable != 1 else ''}."
    )
    if metrics.cost is not None:
        body += (
            f" Inkthreadable postage is known for {metrics.cost_known_orders} of them "
            f"({fmt(metrics.cost)}); the postage result on those orders is {fmt(metrics.net)}."
        )
    else:
        body += " No linked Inkthreadable postage, so supplier cost stays —."
    if metrics.free_postage:
        absorbed = (
            f"{fmt(metrics.free_cost)} of known supplier postage"
            if metrics.free_cost is not None
            else "supplier postage unknown"
        )
        body += (
            f" {metrics.free_postage} order{'s' if metrics.free_postage != 1 else ''} "
            f"went out with free postage ({absorbed})."
        )
    rows.append(
        Insight(
            title="Postage charged versus supplier cost",
            body=body,
            href="/shipping/" + _qs(range_key),
            href_label="Open Shipping",
        )
    )


def _discounts(rows, date_range, range_key):
    metrics = compute_discounts(date_range)
    if not metrics.discounted:
        return
    top = rank_codes(metrics.codes, sort="amount")
    leader = top[0].code if top else None
    body = (
        f"{fmt(metrics.amount)} was given away across {metrics.discounted} "
        f"discounted order{'s' if metrics.discounted != 1 else ''}"
    )
    if metrics.of_list_pct is not None:
        body += f" ({metrics.of_list_pct:.1f}% of list price)"
    body += "."
    if leader:
        body += f" The largest code is {leader} ({fmt(top[0].amount)})."
    if metrics.profit_impact is not None:
        body += f" On orders with a complete cost, that is {fmt(metrics.profit_impact)} of contribution profit."
    else:
        body += " Profit given away stays — until those orders have a cost."
    rows.append(
        Insight(
            title="Discounts given away",
            body=body,
            href="/discounts/" + _qs(range_key),
            href_label="Open Discounts",
        )
    )


def _refunds(rows, date_range, range_key):
    metrics = compute_refunds(date_range)
    if not metrics.refunded_orders and not metrics.amount:
        return
    body = (
        f"{fmt(metrics.amount)} was refunded on {metrics.refunded_orders} of "
        f"{metrics.universe} merchandise order{'s' if metrics.universe != 1 else ''}"
    )
    if metrics.unit_rate_pct is not None:
        body += (
            f" ({metrics.refunded_units:.0f} of {metrics.sold_units:.0f} units, "
            f"{metrics.unit_rate_pct:.1f}%)"
        )
    body += "."
    returned = rank_refund_items(
        [row for row in metrics.products if row.refunded_units or row.amount],
        sort="rate",
    )
    if returned and returned[0].rate_pct is not None:
        top = returned[0]
        body += f" Highest unit rate: {top.label} ({top.rate_pct:.1f}%)."
    body += " Print-on-demand cost is not treated as recovered."
    rows.append(
        Insight(
            title="Refunds on sales in this period",
            body=body,
            href="/refunds/" + _qs(range_key),
            href_label="Open Refunds",
        )
    )


def _top_product(rows, paid_products, range_key):
    if not paid_products:
        return
    top = paid_products[0]
    profit = fmt(top.profit) if top.is_complete else "— (cost missing)"
    href = (
        reverse("web:product_detail", args=[top.product_id]) + _qs(range_key)
        if top.product_id
        else "/products/" + _qs(range_key)
    )
    rows.append(
        Insight(
            title="Best seller by units (paid products only)",
            body=(
                f"{top.label} sold {top.units} unit{'s' if top.units != 1 else ''} "
                f"across {top.orders} order{'s' if top.orders != 1 else ''}, "
                f"for {fmt(top.net_revenue)} net sales. Contribution profit is {profit}."
            ),
            href=href,
            href_label="Open product",
        )
    )


def _concentration(rows, paid_products):
    if len(paid_products) < 3:
        return
    top3 = paid_products[:3]
    units = sum(row.units for row in paid_products)
    share = safe_divide(Decimal(sum(row.units for row in top3)) * 100, Decimal(units))
    if share is None:
        return
    names = ", ".join(row.label for row in top3)
    rows.append(
        Insight(
            title="Sales are concentrated in a few products",
            body=(
                f"The top three paid products ({names}) account for {share:.0f}% of paid units "
                f"in this period ({sum(row.units for row in top3)} of {units})."
            ),
            href="/products/",
            href_label="Open Products",
        )
    )


def _cost_coverage(rows, shop, range_key):
    if not shop.lines_total:
        return
    if shop.is_complete:
        rows.append(
            Insight(
                title="Every sold line has a cost",
                body=(
                    f"Shop contribution profit is {fmt(shop.profit)} "
                    f"({shop.margin_pct:.1f}% margin) on {fmt(shop.net_revenue)} net sales."
                    if shop.margin_pct is not None
                    else f"Shop contribution profit is {fmt(shop.profit)} on {fmt(shop.net_revenue)} net sales."
                ),
                href="/products/" + _qs(range_key),
                href_label="Open Products",
            )
        )
        return
    rows.append(
        Insight(
            title="Shop profit is withheld until mappings are finished",
            body=(
                f"{shop.lines_missing_cost} of {shop.lines_total} sold lines have no cost "
                f"({shop.completeness_pct:.0f}% coverage). Shopify does not store Inkthreadable "
                f"print cost unless Cost per item was entered. Profit stays — rather than a guess."
            ),
            tone="gap",
            href="/mapping/",
            href_label="Open Mapping",
        )
    )


def _wix_era(rows, date_range, range_key):
    if date_range.end < SHOPIFY_LAUNCH_DATE:
        rows.append(
            Insight(
                title="This period is entirely before Shopify",
                body=(
                    f"The storefront moved from Wix on {SHOPIFY_LAUNCH_DATE:%d %b %Y} "
                    f"(first Shopify order #1001). Bank rows here will not have matching "
                    f"Shopify orders."
                ),
                tone="watch",
                href="/reconciliation/" + _qs(range_key),
                href_label="Open Reconciliation",
            )
        )
        return
    if date_range.start >= SHOPIFY_LAUNCH_DATE:
        return
    pre = (
        BankTransaction.objects.filter(
            occurred_on__gte=date_range.start,
            occurred_on__lt=SHOPIFY_LAUNCH_DATE,
        )
        .exclude(category__kind__in=CategoryKind.outside_pnl())
        .count()
    )
    if not pre:
        return
    rows.append(
        Insight(
            title="Part of this range is Wix-era bank activity",
            body=(
                f"{pre} bank transaction{'s' if pre != 1 else ''} sit before "
                f"{SHOPIFY_LAUNCH_DATE:%d %b %Y}, when the shop was on Wix. "
                f"They will not reconcile to Shopify payouts."
            ),
            tone="watch",
            href="/reconciliation/" + _qs(range_key),
            href_label="Open Reconciliation",
        )
    )


def _cash_counterparties(rows, bank, range_key):
    stripe = quantise(sum((t.amount for t in bank if is_payout(t)), ZERO))
    ink = quantise(sum((t.amount for t in bank if is_supplier_charge(t)), ZERO))
    if stripe == ZERO and ink == ZERO:
        return
    rows.append(
        Insight(
            title="Stripe in versus Inkthreadable out",
            body=(
                f"Stripe/Adyen deposits total {fmt(stripe)}. Inkthreadable charges total "
                f"{fmt(ink)}. These are cash movements, not Shopify order totals."
            ),
            href="/reconciliation/" + _qs(range_key),
            href_label="Open Reconciliation",
        )
    )


def _repeat_customers(rows, paid_orders, range_key):
    counts: dict[str, int] = {}
    for order in paid_orders:
        if order.customer_id:
            key = f"id:{order.customer_id}"
        elif order.email:
            key = f"email:{order.email.casefold()}"
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    identified = len(counts)
    repeats = sum(1 for n in counts.values() if n >= 2)
    if not identified:
        return
    rate = safe_divide(Decimal(repeats) * 100, Decimal(identified))
    rows.append(
        Insight(
            title="Repeat buyers among paid orders",
            body=(
                f"{repeats} of {identified} identified customers placed more than one paid order "
                f"in this period"
                + (f" ({rate:.0f}%)." if rate is not None else ".")
            ),
            href="/customers/" + _qs(range_key),
            href_label="Open Customers",
        )
    )


def _payout_links(rows, bank, date_range, range_key):
    payouts = [t for t in bank if is_payout(t)]
    if not payouts:
        return
    linked = CashLink.objects.filter(
        bank_transaction_id__in=[t.pk for t in payouts],
        confidence="confirmed",
    ).count()
    rows.append(
        Insight(
            title="Shopify days matched to Stripe",
            body=(
                f"{linked} of {len(payouts)} Stripe/Adyen deposits in this period have a "
                f"confirmed cash link. Unmatched rows stay unmatched — the app does not invent them."
            ),
            tone="watch" if linked < len(payouts) else "fact",
            href="/reconciliation/" + _qs(range_key),
            href_label="Open Reconciliation",
        )
    )


def _groups(rows, profits, date_range, range_key):
    ensure_default_groups()
    groups = all_group_performance(
        profits,
        ProductGroup.objects.prefetch_related("products"),
        date_range=date_range,
    )
    ranked = [row for row in rank_products(groups, sort="revenue") if row.orders and not row.is_free]
    if len(ranked) < 2:
        return
    leader = ranked[0]
    profit = fmt(leader.profit) if leader.is_complete else "—"
    names = ", ".join(f"{row.label} {fmt(row.net_revenue)}" for row in ranked[:3])
    rows.append(
        Insight(
            title="Which garment group is carrying the shop",
            body=(
                f"{leader.label} leads on net sales ({fmt(leader.net_revenue)}, profit {profit}). "
                f"Top groups: {names}."
            ),
            href="/groups/" + _qs(range_key),
            href_label="Open Groups",
        )
    )


def _loss_makers(rows, paid_products, range_key):
    losers = [row for row in paid_products if row.is_complete and row.profit < 0]
    if not losers:
        return
    losers.sort(key=lambda row: row.profit)
    worst = losers[0]
    rows.append(
        Insight(
            title="Products that lost money after costs",
            body=(
                f"{len(losers)} paid product{'s' if len(losers) != 1 else ''} "
                f"{'have' if len(losers) != 1 else 'has'} a complete cost and a negative "
                f"contribution. Worst is {worst.label} at {fmt(worst.profit)} "
                f"on {fmt(worst.net_revenue)} net sales."
            ),
            tone="watch",
            href="/products/" + _qs(range_key, sort="profit"),
            href_label="Open Products",
        )
    )


def _uncategorised(rows, cash, range_key):
    if not cash.uncategorised_count:
        return
    rows.append(
        Insight(
            title="Bank rows still need a category",
            body=(
                f"{cash.uncategorised_count} transaction"
                f"{'s' if cash.uncategorised_count != 1 else ''} in this period "
                f"({fmt(cash.uncategorised_value)}) are left out of Cash until they have a category."
            ),
            tone="gap",
            href="/categories/",
            href_label="Open Categories",
        )
    )


def _card_fees(rows, cash, shop):
    if cash.payment_fees == ZERO and not shop.payment_fees:
        return
    body = f"Bank payment fees in this period are {fmt(cash.payment_fees)}."
    if shop.orders and shop.payment_fees:
        body += f" Shopify orders allocated {fmt(shop.payment_fees)} of card fees."
    rows.append(
        Insight(
            title="What card fees took",
            body=body,
            href="/",
            href_label="Open Cash",
        )
    )


def _countries(rows, date_range, range_key):
    metrics = compute_shipping(date_range)
    countries = [row for row in rank_countries(metrics.countries, sort="orders") if row.orders]
    if len(countries) < 2:
        return
    top = countries[0]
    rows.append(
        Insight(
            title="Where physical orders are going",
            body=(
                f"{top.country or 'Unknown'} is the largest destination "
                f"({top.orders} physical order{'s' if top.orders != 1 else ''}, "
                f"customers paid {fmt(top.charged)} postage)."
            ),
            href="/shipping/" + _qs(range_key),
            href_label="Open Shipping",
        )
    )
