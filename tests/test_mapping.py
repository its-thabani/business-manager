"""Product and variant mapping — suggestions, confirmation, digital costs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.analytics.profitability import Basis, CostResolver, compute_order_profit, unmapped_variant_report
from apps.catalog.models import Product, ProductVariant, normalise_size
from apps.supplier.mapping import (
    MappingEngine,
    apply_suggestions,
    colours_match,
    confirm_all_for_product,
    create_missing_variants_for_product,
    looks_like_blank_sku,
    mark_product_digital,
    set_product_blank,
    sku_token_match,
    sku_tokens,
)
from apps.supplier.models import (
    MappingConfidence,
    ProductMapping,
    SupplierProduct,
    SupplierVariant,
    SupplierVariantCost,
    VariantMapping,
)

pytestmark = pytest.mark.django_db


def _blank(name="AWDis College Hoodie", supplier_id="JH001", variants=None):
    product = SupplierProduct.objects.create(name=name, supplier_id=supplier_id, brand="AWDis")
    for sku, colour, size, cost in variants or []:
        supplier_variant = SupplierVariant.objects.create(
            product=product, sku=sku, colour=colour, size=size
        )
        if cost is not None:
            SupplierVariantCost.objects.create(
                supplier_variant=supplier_variant,
                unit_cost=Decimal(cost),
                effective_from=date(2020, 1, 1),
            )
    return product


def _shop_product(title, variants, product_type="Hoodie", **kwargs):
    product = Product.objects.create(
        title=title,
        product_type=product_type,
        handle=title.lower().replace(" ", "-")[:80],
        shopify_id=f"gid://shopify/Product/{Product.objects.count() + 100}",
        **kwargs,
    )
    for position, (sku, colour, size, price) in enumerate(variants, start=1):
        variant = ProductVariant(
            product=product,
            position=position,
            title=f"{colour} / {size}".strip(" /"),
            sku=sku,
            price=Decimal(price),
            options={"Colour": colour, "Size": size} if colour or size else {},
            shopify_id=f"gid://shopify/ProductVariant/{ProductVariant.objects.count() + 200 + position}",
        )
        variant.derive_options()
        variant.save()
    return product


def test_sku_tokens_ignore_order():
    assert sku_tokens("JH001-JBK-XS") == ("JH001", frozenset({"JBK", "XS"}))
    assert sku_tokens("OLD-JH001-JBK-M") == ("JH001", frozenset({"JBK", "M"}))
    assert sku_token_match("JH001-JBK-XS", "JH001-XS-JBK")
    assert sku_token_match("AT002-DBL-L", "OLD-AT002-L-DBL")
    assert not sku_token_match("JH001-JBK-XS", "JH030-JBK-XS")


def test_jet_black_matches_black():
    assert colours_match("Jet Black", "Black")
    assert colours_match("Arctic White", "White")
    assert not colours_match("Jet Black", "Navy")


def test_blank_sku_shape():
    assert looks_like_blank_sku("JH001-JBK-XS")
    assert looks_like_blank_sku("AT002-BLK-M")
    assert looks_like_blank_sku("AS-5080-BLK-L")
    assert not looks_like_blank_sku("BIBLE-VERSE-01")


def test_exact_sku_is_preferred(make_order):
    _blank(
        variants=[
            ("JH001-JBK-XS", "Jet Black", "XS", "12.50"),
            ("JH001-AWH-L", "Arctic White", "L", "12.50"),
        ]
    )
    product = _shop_product(
        "Jesus Is My Lifeline Cross Hoodie",
        [("JH001-JBK-XS", "Black", "XS", "34.99")],
    )

    suggestion = MappingEngine().suggest_for_product(product)
    variant_suggestion = suggestion.variant_suggestions[0]

    assert suggestion.supplier_product.supplier_id == "JH001"
    assert variant_suggestion.supplier_variant.sku == "JH001-JBK-XS"
    assert variant_suggestion.reason == "exact SKU"


def test_token_set_matches_when_sku_order_differs():
    _blank(variants=[("JH001-XS-JBK", "Jet Black", "XS", "12.50")])
    product = _shop_product("Hoodie", [("JH001-JBK-XS", "Black", "XS", "34.99")])

    suggestion = MappingEngine().suggest_variant(product.variants.get())

    assert suggestion.supplier_variant.sku == "JH001-XS-JBK"
    assert suggestion.reason == "SKU token set"


def test_size_and_colour_match_inside_the_chosen_blank():
    hoodie = _blank(
        variants=[
            ("JH001-JBK-M", "Jet Black", "M", "12.50"),
            ("JH001-AWH-M", "Arctic White", "M", "12.50"),
        ]
    )
    product = _shop_product("Hoodie", [("", "Black", "Medium", "34.99")])

    suggestion = MappingEngine().suggest_variant(
        product.variants.get(), supplier_product=hoodie
    )

    assert suggestion.supplier_variant.colour == "Jet Black"
    assert suggestion.reason == "size and colour"


def test_a_bible_download_is_flagged_digital():
    product = _shop_product(
        "Bible Verse Wall Art Download",
        [("", "", "", "4.99")],
        product_type="Digital",
        tags=["printable", "download"],
    )

    suggestion = MappingEngine().suggest_for_product(product)

    assert suggestion.no_supplier
    assert "digital" in suggestion.reason.lower()


def test_that_in_a_title_does_not_count_as_a_hat():
    product = _shop_product(
        "8 Bible Verses That Will Make You Smile",
        [("", "", "", "4.99")],
        product_type="",
    )

    suggestion = MappingEngine().suggest_for_product(product)

    assert suggestion.no_supplier


def test_a_hoodie_without_a_sku_is_not_called_digital():
    """One-off garments often have no options. That is not the same as a download."""
    product = _shop_product(
        "Faith Over Fear Black Pullover Hoodie (XL)",
        [("", "", "", "34.99")],
        product_type="",
    )

    suggestion = MappingEngine().suggest_for_product(product)

    assert not suggestion.no_supplier


def test_an_unlisted_apparel_sku_is_not_called_digital():
    """AT002 is a real blank even when Inkthreadable has never sent a catalogue row."""
    product = _shop_product(
        "StayLit Classic Tee",
        [("AT002-BLK-L", "Black", "L", "24.99")],
        product_type="T-Shirt",
    )

    suggestion = MappingEngine().suggest_for_product(product)

    assert not suggestion.no_supplier
    assert suggestion.supplier_product is None


def test_unknown_blank_prefix_is_not_forced_onto_another_garment():
    """A Black / L AT002 tee must not inherit a Rocker cost just because sizes match."""
    _blank(
        name="Rocker",
        supplier_id="STTU758",
        variants=[("STTU758-BLK-L", "Black", "L", "8.00")],
    )
    product = _shop_product(
        "Classic Tee",
        [("AT002-BLK-L", "Black", "L", "24.99")],
        product_type="T-Shirt",
    )

    suggestion = MappingEngine().suggest_for_product(product)

    assert suggestion.supplier_product is None
    assert suggestion.variant_suggestions[0].supplier_variant is None


def test_a_stale_suggestion_is_cleared_when_it_is_no_longer_justified():
    _blank(
        name="Rocker",
        supplier_id="STTU758",
        variants=[("STTU758-BLK-L", "Black", "L", "8.00")],
    )
    product = _shop_product(
        "Classic Tee",
        [("AT002-BLK-L", "Black", "L", "24.99")],
        product_type="T-Shirt",
    )
    ProductMapping.objects.create(
        product=product,
        supplier_product=SupplierProduct.objects.get(name="Rocker"),
        confidence=MappingConfidence.SUGGESTED,
        match_reason="wrong",
    )

    apply_suggestions(product=product)

    assert not ProductMapping.objects.filter(product=product).exists()


def test_apply_suggestions_does_not_overwrite_a_confirmed_mapping():
    _blank(variants=[("JH001-JBK-XS", "Jet Black", "XS", "12.50")])
    product = _shop_product("Hoodie", [("JH001-JBK-XS", "Black", "XS", "34.99")])
    wrong = SupplierProduct.objects.create(name="Wrong blank")
    ProductMapping.objects.create(
        product=product,
        supplier_product=wrong,
        confidence=MappingConfidence.CONFIRMED,
        match_reason="I chose this",
    )

    apply_suggestions(product=product)

    product.refresh_from_db()
    assert product.supplier_product_mapping.supplier_product == wrong
    assert product.supplier_product_mapping.is_confirmed


def test_apply_suggestions_writes_sku_matches():
    _blank(variants=[("JH001-JBK-XS", "Jet Black", "XS", "12.50")])
    product = _shop_product("Hoodie", [("JH001-JBK-XS", "Black", "XS", "34.99")])

    result = apply_suggestions(product=product)

    assert result.products_created == 1
    assert result.variants_created == 1
    mapping = product.variants.get().supplier_mapping
    assert mapping.supplier_variant.sku == "JH001-JBK-XS"
    assert mapping.confidence == MappingConfidence.SUGGESTED


def test_digital_products_cost_zero_not_unknown(make_order):
    product = _shop_product("Verse card", [("", "", "", "3.00")], product_type="Digital")
    mark_product_digital(product)
    variant = product.variants.get()
    order = make_order(lines=[(variant, 1, "3.00")], shipping_charged="0.00")
    order.lines.update(requires_shipping=False)

    resolver = CostResolver([variant.pk])
    cost, basis = resolver.unit_cost(variant.pk, date(2026, 6, 15))
    assert cost == Decimal("0.00")
    assert basis == Basis.NOT_APPLICABLE
    assert resolver.is_mapped(variant.pk)

    profit = compute_order_profit(order)
    assert profit.supplier_product_cost == Decimal("0.00")
    assert profit.supplier_product_cost_basis == Basis.NOT_APPLICABLE
    assert profit.supplier_shipping_cost == Decimal("0.00")
    assert profit.contribution_profit is not None
    assert profit.is_complete


def test_unmapped_report_treats_digital_as_mapped(make_order):
    product = _shop_product("Download", [("", "", "", "3.00")], product_type="Digital")
    mark_product_digital(product)
    make_order(lines=[(product.variants.get(), 2, "3.00")])

    report = unmapped_variant_report()

    assert report["mapped_units"] == 2
    assert report["unmapped_units"] == 0


def test_create_missing_variants_fills_a_blank_with_no_catalogue(make_order):
    blank = _blank(name="AWDis T-Shirt", supplier_id="AT002", variants=[])
    product = _shop_product(
        "Classic Tee",
        [("AT002-BLK-L", "Black", "L", "24.99"), ("AT002-WHT-M", "White", "M", "24.99")],
        product_type="T-Shirt",
    )
    set_product_blank(product, blank)

    created = create_missing_variants_for_product(product, unit_cost=Decimal("8.10"))

    assert len(created) == 2
    variant = product.variants.get(sku="AT002-BLK-L")
    assert variant.supplier_mapping.supplier_variant.sku == "AT002-BLK-L"
    assert variant.cost_on(date(2026, 6, 15)) == Decimal("8.10")


def test_confirm_all_promotes_suggestions():
    _blank(variants=[("JH001-JBK-XS", "Jet Black", "XS", "12.50")])
    product = _shop_product("Hoodie", [("JH001-JBK-XS", "Black", "XS", "34.99")])
    apply_suggestions(product=product)

    confirm_all_for_product(product)

    assert product.supplier_product_mapping.is_confirmed
    assert product.variants.get().supplier_mapping.is_confirmed


def test_suggest_mappings_command_is_idempotent():
    _blank(variants=[("JH001-JBK-XS", "Jet Black", "XS", "12.50")])
    _shop_product("Hoodie", [("JH001-JBK-XS", "Black", "XS", "34.99")])

    call_command("suggest_mappings")
    call_command("suggest_mappings")

    assert VariantMapping.objects.count() == 1
    assert ProductMapping.objects.count() == 1


def test_mapping_page_lists_unmapped_bestsellers(client, make_order):
    product = _shop_product("Bestseller Hoodie", [("JH001-JBK-XS", "Black", "XS", "34.99")])
    make_order(lines=[(product.variants.get(), 4, "34.99")])

    response = client.get("/mapping/")

    assert response.status_code == 200
    assert b"Bestseller Hoodie" in response.content
    assert b"unmapped" in response.content


def test_mapping_page_hides_unsold_drafts_by_default(client):
    from apps.catalog.models import ProductStatus

    Product.objects.create(
        title="Old draft listing",
        handle="old-draft-listing",
        status=ProductStatus.DRAFT,
        shopify_id="gid://shopify/Product/draft-hide",
    )

    hidden = client.get("/mapping/?filter=all")
    shown = client.get("/mapping/?filter=all&drafts=1")

    assert hidden.status_code == shown.status_code == 200
    assert b"Old draft listing" not in hidden.content
    assert b"Old draft listing" in shown.content


def test_mapping_page_can_mark_a_product_digital(client):
    product = _shop_product("Verse card", [("", "", "", "3.00")], product_type="Digital")

    response = client.post(f"/mapping/{product.pk}/", {"action": "mark_digital"})

    assert response.status_code == 302
    mapping = ProductMapping.objects.get(product=product)
    assert mapping.no_supplier
    assert mapping.is_confirmed


def test_size_alias_2x_large():
    assert normalise_size("2x-large") == "2XL"
    assert normalise_size("2xlarge") == "2XL"
