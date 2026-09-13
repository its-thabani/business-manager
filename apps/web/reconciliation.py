"""Views for matching Shopify sales days and supplier invoices to bank cash."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.analytics.reconciliation import (
    apply_suggestions,
    clear_link,
    confirm_link,
    confirm_suggestion,
    find_live_suggestion,
    reconcile,
)
from apps.core.periods import PRESETS
from apps.finance.models import CashLink, CashLinkKind
from apps.web.charts import column_chart
from apps.web.views import _requested_range


def reconciliation(request):
    date_range, preset = _requested_range(request)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "suggest":
            result = apply_suggestions()
            messages.success(
                request,
                (
                    f"Suggestions written: {result['created']} new, "
                    f"{result['updated']} updated, {result['skipped']} confirmed left alone."
                ),
            )
        elif action == "confirm" and request.POST.get("link_id"):
            confirm_link(get_object_or_404(CashLink, pk=request.POST["link_id"]))
            messages.success(request, "Match confirmed.")
        elif action == "confirm" and request.POST.get("transaction_id"):
            suggestion = find_live_suggestion(
                int(request.POST["transaction_id"]),
                request.POST.get("kind") or CashLinkKind.SHOPIFY_DAY,
            )
            if suggestion is None:
                messages.error(request, "That match is no longer available.")
            else:
                confirm_suggestion(suggestion)
                messages.success(request, "Match confirmed.")
        elif action == "confirm_all":
            result = apply_suggestions()
            CashLink.objects.filter(confidence="suggested").update(confidence="confirmed")
            messages.success(
                request,
                f"Confirmed every current suggestion ({result['created'] + result['updated']} rows).",
            )
        elif action == "clear" and request.POST.get("link_id"):
            clear_link(get_object_or_404(CashLink, pk=request.POST["link_id"]))
            messages.success(request, "Match cleared.")
        return redirect(f"{request.path}?range={preset}")

    report = reconcile(date_range)
    months = column_chart(report.months, ["expected", "payouts", "supplier"])
    return render(
        request,
        "web/reconciliation.html",
        {
            "nav": "reconciliation",
            "date_range": date_range,
            "presets": PRESETS,
            "selected_preset": preset,
            "report": report,
            "months": months,
        },
    )
