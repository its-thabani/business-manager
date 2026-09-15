"""Views for reviewing and confirming product / variant mappings."""

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.catalog.models import Product, ProductStatus, ProductVariant
from apps.sales.models import OrderLine
from apps.supplier.mapping import (
    MappingEngine,
    apply_suggestions,
    confirm_all_for_product,
    confirm_variant_mapping,
    create_missing_variants_for_product,
    create_supplier_blank,
    create_supplier_variant,
    mark_product_digital,
    parse_money,
    set_product_blank,
    set_variant_mapping,
)
from apps.supplier.models import (
    MappingConfidence,
    ProductMapping,
    SupplierProduct,
    SupplierVariant,
    VariantMapping,
)


def mapping_list(request):
    """Products ranked by units sold, with how much of each still needs a cost."""
    if request.method == "POST" and request.POST.get("action") == "suggest_all":
        result = apply_suggestions()
        messages.success(request, f"Suggestions written: {result}")
        return redirect("web:mapping_list")

    products = list(
        Product.objects.select_related(
            "supplier_product_mapping",
            "supplier_product_mapping__supplier_product",
        )
        .prefetch_related("variants")
        .annotate(
            unit_count=Sum(
                "order_lines__quantity",
                filter=Q(order_lines__order__is_test=False, order_lines__order__cancelled_at__isnull=True),
            ),
            sold_variant_count=Count(
                "order_lines__variant",
                distinct=True,
                filter=Q(order_lines__order__is_test=False, order_lines__order__cancelled_at__isnull=True),
            ),
        )
        .order_by("-unit_count", "title")
    )

    mapped_ids = set(VariantMapping.objects.values_list("product_variant_id", flat=True))
    digital_ids = set(
        ProductMapping.objects.filter(no_supplier=True).values_list("product_id", flat=True)
    )
    sold_units = (
        OrderLine.objects.filter(order__is_test=False, order__cancelled_at__isnull=True)
        .values("variant_id", "product_id")
        .annotate(units=Sum("quantity"))
    )
    units_by_product: dict[int, int] = defaultdict(int)
    mapped_units_by_product: dict[int, int] = defaultdict(int)
    for row in sold_units:
        product_id = row["product_id"]
        units = row["units"] or 0
        units_by_product[product_id] += units
        if product_id in digital_ids or row["variant_id"] in mapped_ids:
            mapped_units_by_product[product_id] += units

    rows = []
    for product in products:
        mapping = getattr(product, "supplier_product_mapping", None)
        variants = list(product.variants.all())
        mapped_variants = sum(1 for variant in variants if variant.pk in mapped_ids)
        units = units_by_product.get(product.pk, 0)
        units_with_cost = mapped_units_by_product.get(product.pk, 0)
        status = _row_status(mapping, variants, mapped_variants, units, units_with_cost)
        rows.append(
            {
                "product": product,
                "mapping": mapping,
                "status": status,
                "units": units,
                "units_with_cost": units_with_cost,
                "mapped_variants": mapped_variants,
                "variant_count": len(variants),
            }
        )

    selected = request.GET.get("filter", "needs_review")
    include_drafts = request.GET.get("drafts") == "1"
    if not include_drafts:
        rows = [
            row
            for row in rows
            if row["product"].status == ProductStatus.ACTIVE or row["units"]
        ]
    totals = {
        "sold_units": sum(units_by_product.values()),
        "units_with_cost": sum(mapped_units_by_product.values()),
        "needs_review": sum(
            1 for row in rows if row["status"] in {"unmapped", "partial", "suggested", "uncertain"}
        ),
        "digital": ProductMapping.objects.filter(no_supplier=True).count(),
    }
    missing_units = totals["sold_units"] - totals["units_with_cost"]
    if selected != "all":
        rows = [row for row in rows if _matches_filter(row, selected)]

    return render(
        request,
        "web/mapping_list.html",
        {
            "nav": "mapping",
            "rows": rows,
            "selected_filter": selected,
            "include_drafts": include_drafts,
            "totals": totals,
            "missing_units": missing_units,
        },
    )


def _row_status(mapping, variants, mapped_variants, units, units_with_cost):
    if mapping and mapping.no_supplier:
        return "digital"
    if mapping and mapping.is_confirmed and units and units_with_cost >= units:
        return "confirmed"
    if mapping and mapping.is_confirmed and mapped_variants == len(variants) and variants:
        return "confirmed"
    if units and units_with_cost == 0 and mapping is None:
        return "unmapped"
    if units and units_with_cost < units:
        return "partial"
    if mapping and mapping.confidence == MappingConfidence.UNCERTAIN:
        return "uncertain"
    if mapping:
        return "suggested"
    if mapped_variants:
        return "partial"
    return "unmapped"


def _matches_filter(row, selected: str) -> bool:
    if selected == "needs_review":
        return row["status"] in {"unmapped", "partial", "suggested", "uncertain"}
    if selected == "digital":
        return row["status"] == "digital"
    if selected == "confirmed":
        return row["status"] == "confirmed"
    return True


def mapping_detail(request, pk: int):
    product = get_object_or_404(
        Product.objects.select_related(
            "supplier_product_mapping",
            "supplier_product_mapping__supplier_product",
        ).prefetch_related(
            "variants",
            "variants__supplier_mapping",
            "variants__supplier_mapping__supplier_variant",
            "variants__supplier_mapping__supplier_variant__product",
            "variants__supplier_mapping__supplier_variant__costs",
        ),
        pk=pk,
    )
    if request.method == "POST":
        return _handle_detail_post(request, product)

    mapping = getattr(product, "supplier_product_mapping", None)
    engine = MappingEngine()
    suggestion = engine.suggest_for_product(product)
    suggestion_by_variant = {row.variant.pk: row for row in suggestion.variant_suggestions}

    sold = {
        row["variant_id"]: row["units"]
        for row in (
            OrderLine.objects.filter(
                product=product,
                order__is_test=False,
                order__cancelled_at__isnull=True,
            )
            .values("variant_id")
            .annotate(units=Sum("quantity"))
        )
    }

    blanks = SupplierProduct.objects.annotate(variant_count=Count("variants")).order_by("name")
    supplier_variants = []
    if mapping and mapping.supplier_product_id:
        supplier_variants = list(
            SupplierVariant.objects.filter(product=mapping.supplier_product).select_related("product")
        )

    variant_rows = []
    for variant in product.variants.all():
        existing = getattr(variant, "supplier_mapping", None)
        proposed = suggestion_by_variant.get(variant.pk)
        variant_rows.append(
            {
                "variant": variant,
                "mapping": existing,
                "suggestion": proposed,
                "units": sold.get(variant.pk, 0),
                "cost": existing.cost_on(timezone.localdate()) if existing else None,
            }
        )
    variant_rows.sort(key=lambda row: (-row["units"], row["variant"].position))

    return render(
        request,
        "web/mapping_detail.html",
        {
            "nav": "mapping",
            "product": product,
            "mapping": mapping,
            "suggestion": suggestion,
            "variant_rows": variant_rows,
            "blanks": blanks,
            "supplier_variants": supplier_variants,
        },
    )


def _handle_detail_post(request, product: Product):
    action = request.POST.get("action", "")
    try:
        _run_action(request, product, action)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("web:mapping_detail", pk=product.pk)
    return redirect("web:mapping_detail", pk=product.pk)


def _run_action(request, product: Product, action: str) -> None:
    if action == "set_blank":
        blank = get_object_or_404(SupplierProduct, pk=request.POST.get("supplier_product_id"))
        set_product_blank(product, blank)
        messages.success(request, f"This product is printed on {blank}.")
        return
    if action == "mark_digital":
        mark_product_digital(product)
        messages.success(request, "Marked as digital / no Inkthreadable cost. Product cost is £0.")
        return
    if action == "clear_product":
        ProductMapping.objects.filter(product=product).delete()
        messages.success(request, "Cleared the product-level mapping.")
        return
    if action == "apply_suggestions":
        result = apply_suggestions(product=product)
        messages.success(request, f"Suggestions written: {result}")
        return
    if action == "confirm_all":
        count = confirm_all_for_product(product)
        messages.success(request, f"Confirmed {count} mapping(s).")
        return
    if action == "confirm_variant":
        variant = _product_variant(product, request.POST.get("variant_id"))
        if confirm_variant_mapping(variant):
            messages.success(request, f"Confirmed {variant.display_options}.")
        else:
            raise ValueError("That variant has no mapping to confirm.")
        return
    if action == "set_variant":
        variant = _product_variant(product, request.POST.get("variant_id"))
        supplier_variant = get_object_or_404(
            SupplierVariant, pk=request.POST.get("supplier_variant_id")
        )
        set_variant_mapping(variant, supplier_variant)
        messages.success(request, f"Mapped {variant.display_options} → {supplier_variant}.")
        return
    if action == "clear_variant":
        variant = _product_variant(product, request.POST.get("variant_id"))
        VariantMapping.objects.filter(product_variant=variant).delete()
        messages.success(request, f"Cleared the mapping for {variant.display_options}.")
        return
    if action == "set_override":
        variant = _product_variant(product, request.POST.get("variant_id"))
        mapping = getattr(variant, "supplier_mapping", None)
        if mapping is None:
            raise ValueError("Map the variant before setting a cost override.")
        amount = parse_money(request.POST.get("cost_override", ""))
        if amount is None:
            raise ValueError("Enter a cost override amount.")
        mapping.cost_override = amount
        mapping.save(update_fields=["cost_override", "updated_at"])
        messages.success(request, f"Override on {variant.display_options} is £{amount}.")
        return
    if action == "clear_override":
        variant = _product_variant(product, request.POST.get("variant_id"))
        mapping = getattr(variant, "supplier_mapping", None)
        if mapping is None:
            raise ValueError("That variant has no mapping.")
        mapping.cost_override = None
        mapping.save(update_fields=["cost_override", "updated_at"])
        messages.success(request, f"Cleared the override on {variant.display_options}.")
        return
    if action == "create_blank":
        name = (request.POST.get("name") or "").strip()
        if not name:
            raise ValueError("A blank needs a name.")
        blank = create_supplier_blank(
            name=name,
            supplier_id=request.POST.get("supplier_id", ""),
            brand=request.POST.get("brand", ""),
        )
        set_product_blank(product, blank)
        messages.success(request, f"Created {blank} and assigned it to this product.")
        return
    if action == "create_variant":
        mapping = getattr(product, "supplier_product_mapping", None)
        blank = mapping.supplier_product if mapping else None
        if blank is None:
            raise ValueError("Assign a supplier blank first.")
        variant = _product_variant(product, request.POST.get("variant_id"))
        amount = parse_money(request.POST.get("unit_cost", ""))
        supplier_variant = create_supplier_variant(
            product=blank,
            sku=request.POST.get("sku") or variant.sku,
            size=request.POST.get("size") or variant.size,
            colour=request.POST.get("colour") or variant.colour,
            unit_cost=amount,
        )
        set_variant_mapping(
            variant,
            supplier_variant,
            reason="Created from the mapping UI",
        )
        messages.success(request, f"Created supplier variant {supplier_variant} at £{amount or '—'}.")
        return
    if action == "create_missing":
        amount = parse_money(request.POST.get("unit_cost", ""))
        if amount is None:
            raise ValueError("Enter a unit cost for the missing variants.")
        created = create_missing_variants_for_product(product, unit_cost=amount)
        messages.success(
            request,
            f"Created {len(created)} supplier variant(s) at £{amount} each.",
        )
        return
    raise ValueError(f"Unknown action: {action}")


def _product_variant(product: Product, variant_id) -> ProductVariant:
    return get_object_or_404(ProductVariant, pk=variant_id, product=product)


def blank_list(request):
    """Inkthreadable blanks already in the database, for mapping later."""
    if request.method == "POST" and request.POST.get("action") == "create_blank":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "A blank needs a name.")
            return redirect("web:blank_list")
        code = (request.POST.get("supplier_id") or "").strip()
        if code and SupplierProduct.objects.filter(supplier_id=code).exists():
            messages.error(request, f"A blank with code {code} is already in the list.")
            return redirect("web:blank_list")
        blank = create_supplier_blank(
            name=name,
            supplier_id=code,
            brand=request.POST.get("brand", ""),
        )
        messages.success(
            request,
            f"Created {blank}. Open a t-shirt on Mapping, assign this blank, then create missing sizes with a unit cost.",
        )
        return redirect("web:blank_detail", pk=blank.pk)

    query = (request.GET.get("q") or "").strip()
    blanks = SupplierProduct.objects.annotate(
        variant_count=Count("variants"),
        mapped_products=Count("product_mappings", distinct=True),
    )
    if query:
        blanks = blanks.filter(
            Q(name__icontains=query) | Q(supplier_id__icontains=query) | Q(brand__icontains=query)
        )
    blanks = blanks.order_by("name")
    return render(
        request,
        "web/blank_list.html",
        {
            "nav": "blanks",
            "blanks": blanks,
            "query": query,
            "variant_total": SupplierVariant.objects.count(),
        },
    )


def blank_detail(request, pk: int):
    blank = get_object_or_404(
        SupplierProduct.objects.prefetch_related(
            "variants",
            "variants__costs",
            "variants__mappings",
            "product_mappings__product",
        ),
        pk=pk,
    )
    variants = []
    for variant in blank.variants.all():
        variants.append(
            {
                "variant": variant,
                "cost": variant.current_cost,
                "mapped": len(variant.mappings.all()),
            }
        )
    return render(
        request,
        "web/blank_detail.html",
        {
            "nav": "blanks",
            "blank": blank,
            "variants": variants,
            "products": [row.product for row in blank.product_mappings.all()],
        },
    )
