"""What-if page. Assumptions are query parameters so a scenario can be bookmarked."""

from __future__ import annotations

from django.shortcuts import render

from apps.analytics.profitability import compute_order_profits
from apps.analytics.simulations import simulate, spec_from_query
from apps.catalog.models import Product
from apps.core.periods import PRESETS
from apps.sales.models import OrderLine
from apps.web.charts import column_chart
from apps.web.views import _requested_range


def simulate_view(request):
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
                "label": "Supplier cost",
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
