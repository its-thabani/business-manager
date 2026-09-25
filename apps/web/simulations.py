"""What-if page. Assumptions are query parameters so a scenario can be bookmarked."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import F, Sum
from django.shortcuts import redirect, render

from apps.analytics.pricing import quote_new_product
from apps.analytics.profitability import compute_order_profits
from apps.analytics.simulations import simulate, spec_from_query
from apps.catalog.models import Product
from apps.core.money import quantise
from apps.core.periods import PRESETS
from apps.sales.models import OrderLine
from apps.web.charts import column_chart
from apps.web.views import _requested_range


def _money(value: str | None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return quantise(Decimal(str(value)))


def _new_product_quote(params):
    if not params.get("printer_product") and not params.get("printer_postage"):
        return None, ""
    try:
        product_net = _money(params.get("printer_product"))
        postage_net = _money(params.get("printer_postage"))
        if product_net is None or postage_net is None:
            return None, "Enter the printer’s product price and postage, both before VAT."
        markup = _money(params.get("markup")) or Decimal("40")
        charged = _money(params.get("customer_postage"))
        if charged is None:
            charged = Decimal("4.99")
        quote = quote_new_product(
            name=params.get("quote_name") or "New product",
            product_net=product_net,
            postage_net=postage_net,
            markup_pct=markup,
            customer_postage=charged,
        )
    except Exception:
        return None, "Those amounts could not be read. Use pounds, for example 18.50."
    return quote, ""


def _hoodie_average_price() -> Decimal | None:
    """Typical hoodie selling price, so a new jacket can be compared with one."""
    totals = OrderLine.objects.filter(
        product__title__icontains="hoodie",
        unit_price__gt=0,
        order__is_test=False,
        order__cancelled_at__isnull=True,
    ).aggregate(units=Sum("quantity"), takings=Sum(F("unit_price") * F("quantity")))
    units = totals["units"] or 0
    if not units or not totals["takings"]:
        return None
    return quantise(totals["takings"] / units)


def simulate_view(request):
    if request.GET.get("quote") == "1":
        return redirect("web:price")
    date_range, preset = _requested_range(request)
    spec = spec_from_query(request.GET)
    profits = compute_order_profits(date_range)
    product = None
    label = "Shop"
    if spec.product_id:
        product = Product.objects.filter(pk=spec.product_id).first()
        if product is None:
            spec.product_id = None
        else:
            label = product.title

    result = simulate(profits, spec, date_range=date_range, label=label)
    comparison = column_chart(
        [
            {
                "label": "Revenue",
                "now": result.baseline.net_revenue,
                "then": result.projected.net_revenue,
            },
            {
                "label": "Profit",
                "now": result.baseline.profit if result.baseline.is_complete else 0,
                "then": result.projected.profit if result.projected.is_complete else 0,
            },
            {
                "label": "Printer product",
                "now": result.baseline.supplier_cost,
                "then": result.projected.supplier_cost,
            },
        ],
        ["now", "then"],
    )
    products = Product.objects.filter(
        pk__in=OrderLine.objects.filter(
            order__is_test=False,
            order__cancelled_at__isnull=True,
            order__placed_on__gte=date_range.start,
            order__placed_on__lte=date_range.end,
        ).values_list("product_id", flat=True)
    ).order_by("title")
    return render(
        request,
        "web/simulate.html",
        {
            "nav": "simulate",
            "section": "insights",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "spec": spec,
            "result": result,
            "product": product,
            "products": products,
            "comparison": comparison,
        },
    )


def price_view(request):
    quote, quote_error = _new_product_quote(request.GET)
    hoodie_price = _hoodie_average_price() if quote else None
    quote_note = ""
    if quote and hoodie_price is not None:
        gap = quote.recommended.shelf_price - hoodie_price
        if gap > Decimal("8"):
            quote_note = (
                f"Hoodies you already sell average about £{hoodie_price}. "
                f"£{quote.recommended.shelf_price} is £{gap} more, so the jacket is only "
                f"worth releasing if people will pay that."
            )
        elif gap > Decimal("2"):
            quote_note = (
                f"A little above the hoodies you already sell (about £{hoodie_price})."
            )
        else:
            quote_note = (
                f"In the same band as the hoodies you already sell (about £{hoodie_price})."
            )
    return render(
        request,
        "web/price.html",
        {
            "nav": "price",
            "section": "insights",
            "quote": quote,
            "quote_error": quote_error,
            "quote_note": quote_note,
            "quote_name": request.GET.get("quote_name", ""),
            "printer_product": request.GET.get("printer_product", ""),
            "printer_postage": request.GET.get("printer_postage", ""),
            "markup": request.GET.get("markup", "40"),
            "customer_postage": request.GET.get("customer_postage", "4.99"),
        },
    )
