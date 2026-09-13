"""In-app editor for bank categorisation rules."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.finance.categorisation import categorise
from apps.finance.models import (
    AmountCondition,
    BankTransaction,
    Category,
    CategoryRule,
    MatchField,
    MatchType,
)


def _rule_form_context(rule: CategoryRule | None = None) -> dict:
    return {
        "rule": rule,
        "categories": Category.objects.filter(is_active=True),
        "match_types": MatchType.choices,
        "match_fields": MatchField.choices,
        "amount_conditions": AmountCondition.choices,
    }


def _apply_form(rule: CategoryRule, post) -> CategoryRule:
    rule.name = (post.get("name") or "").strip()
    rule.pattern = (post.get("pattern") or "").strip()
    rule.match_type = post.get("match_type") or MatchType.CONTAINS
    rule.match_field = post.get("match_field") or MatchField.ANY_TEXT
    rule.amount_condition = post.get("amount_condition") or AmountCondition.ANY
    rule.category = get_object_or_404(Category, pk=post.get("category"))
    try:
        rule.priority = int(post.get("priority") or 100)
    except (TypeError, ValueError):
        rule.priority = 100
    rule.is_active = post.get("is_active") in {"1", "on", "true", "yes"}
    return rule


def category_rules(request):
    if request.method == "POST" and request.POST.get("action") == "apply":
        result = categorise()
        messages.success(
            request,
            (
                f"Examined {result.examined}, changed {result.changed}, "
                f"skipped {result.locked_skipped} locked, "
                f"{result.uncategorised} still need a category."
            ),
        )
        return redirect("web:category_rules")

    if request.method == "POST" and request.POST.get("action") == "add":
        pattern = (request.POST.get("pattern") or "").strip()
        if not pattern:
            messages.error(request, "A rule needs a pattern to match.")
            return redirect("web:category_rules")
        rule = _apply_form(CategoryRule(), request.POST)
        rule.save()
        messages.success(request, f"Rule added: {rule}.")
        return redirect("web:category_rules")

    rules = list(CategoryRule.objects.select_related("category"))
    uncategorised = BankTransaction.objects.filter(category__isnull=True).count()
    return render(
        request,
        "web/categories.html",
        {
            "nav": "categories",
            "rules": rules,
            "uncategorised": uncategorised,
            **_rule_form_context(),
        },
    )


def category_rule_edit(request, pk: int):
    rule = get_object_or_404(CategoryRule.objects.select_related("category"), pk=pk)
    if request.method == "POST" and request.POST.get("action") == "delete":
        label = str(rule)
        rule.delete()
        messages.success(request, f"Deleted {label}. Locked bank rows keep the category they already have.")
        return redirect("web:category_rules")

    if request.method == "POST":
        pattern = (request.POST.get("pattern") or "").strip()
        if not pattern:
            messages.error(request, "A rule needs a pattern to match.")
            return render(request, "web/category_rule_form.html", {"nav": "categories", **_rule_form_context(rule)})
        _apply_form(rule, request.POST)
        rule.save()
        messages.success(request, f"Saved {rule}.")
        return redirect("web:category_rules")

    return render(
        request,
        "web/category_rule_form.html",
        {"nav": "categories", **_rule_form_context(rule)},
    )
