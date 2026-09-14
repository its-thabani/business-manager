"""Suggest and apply Shopify → Inkthreadable product/variant mappings.

Shopify and Inkthreadable name the same garment differently. A hoodie sold as
"Black / XS" with SKU ``JH001-JBK-XS`` is Inkthreadable's "Jet Black / XS" on
the AWDis College Hoodie. Token order in the SKU is also inconsistent
(``JH001-JBK-XS`` vs ``JH001-XS-JBK``). This module matches in a fixed order
of trustworthiness and never overwrites a mapping you have confirmed.

Digital products (downloads, templates, wall art) have no Inkthreadable cost.
They are marked ``no_supplier`` so they leave the unmapped queue and so profit
treats their product cost as genuinely zero — not as missing, and not as a
guessed £0 on an unmapped hoodie.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import QuerySet

from apps.catalog.models import Product, ProductVariant, normalise_size
from apps.core.money import ZERO, to_decimal
from apps.supplier.models import (
    MappingConfidence,
    ProductMapping,
    SupplierProduct,
    SupplierVariant,
    SupplierVariantCost,
    VariantMapping,
)

# Colours that are the same garment with different shop vs supplier names.
# Only aliases we are sure about: a wrong colour match silently costs the
# wrong amount.
_COLOUR_ALIASES = {
    "black": "black",
    "jet black": "black",
    "jbk": "black",
    "white": "white",
    "arctic white": "white",
    "awh": "white",
    "navy": "navy",
    "french navy": "navy",
    "fnv": "navy",
    "heather grey": "heather grey",
    "heather gray": "heather grey",
    "athletic heather": "heather grey",
    "sports grey": "heather grey",
    "sport grey": "heather grey",
    "heather": "heather grey",
}

_DIGITAL_HINTS = (
    "digital",
    "download",
    "printable",
    "printables",
    "template",
    "templates",
    "wall art",
    "instant download",
    "svg",
    "png file",
)

_APPAREL_TYPES = {
    "t-shirt",
    "tshirt",
    "tee",
    "hoodie",
    "sweatshirt",
    "jumper",
    "sweater",
    "hat",
    "cap",
    "mug",
    "tote",
    "bag",
    "onesie",
    "baby",
}

# Title words that mean a physical item, even when Shopify sent no SKU or size.
_PHYSICAL_TITLE_HINTS = (
    "hoodie",
    "sweatshirt",
    "t-shirt",
    "tshirt",
    "cap",
    "snapback",
    "tote",
    "mug",
    "hat",
    "onesie",
    "notebook",
    "journal",
    "card",
    "tote bag",
    "baseball",
)

# Letter-plus-digits blank codes even when that blank is not in the catalogue yet.
_BLANK_SKU = re.compile(r"^[A-Z]{2,3}-?\d{3,4}\b")


def canonical_colour(value: str) -> str:
    """Lower-case colour with known shop/supplier aliases applied."""
    cleaned = " ".join((value or "").split()).casefold()
    return _COLOUR_ALIASES.get(cleaned, cleaned)


def colours_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return canonical_colour(left) == canonical_colour(right)


def sizes_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return normalise_size(left) == normalise_size(right)


def sku_tokens(sku: str) -> tuple[str, frozenset[str]] | None:
    """Split a SKU into (prefix, remaining tokens) so order does not matter.

    ``JH001-JBK-XS`` and ``JH001-XS-JBK`` both become ``('JH001', {'JBK', 'XS'})``.
    """
    parts = [p for p in (sku or "").upper().replace("_", "-").split("-") if p]
    if not parts:
        return None
    if parts[0] == "OLD":
        parts = parts[1:]
    if not parts:
        return None
    return parts[0], frozenset(parts[1:])


def sku_token_match(shop_sku: str, supplier_sku: str) -> bool:
    left = sku_tokens(shop_sku)
    right = sku_tokens(supplier_sku)
    if not left or not right:
        return False
    return left[0] == right[0] and left[1] == right[1]


def looks_like_blank_sku(sku: str) -> bool:
    return bool(_BLANK_SKU.match((sku or "").upper().replace("_", "-")))


def _text_has_hint(text: str, hints: tuple[str, ...]) -> bool:
    """True when a hint appears as a whole word (or an exact phrase).

    Short hints like ``hat`` must not match inside ``That``, or Bible verse
    downloads get treated as physical products.
    """
    haystack = (text or "").casefold()
    for hint in hints:
        if " " in hint:
            if hint in haystack:
                return True
        elif re.search(rf"\b{re.escape(hint)}\b", haystack):
            return True
    return False


@dataclass
class VariantSuggestion:
    variant: ProductVariant
    supplier_variant: SupplierVariant | None
    confidence: str
    reason: str


@dataclass
class ProductSuggestion:
    product: Product
    supplier_product: SupplierProduct | None = None
    no_supplier: bool = False
    confidence: str = MappingConfidence.SUGGESTED
    reason: str = ""
    variant_suggestions: list[VariantSuggestion] = field(default_factory=list)


@dataclass
class ApplyResult:
    products_created: int = 0
    products_updated: int = 0
    products_skipped: int = 0
    variants_created: int = 0
    variants_updated: int = 0
    variants_skipped: int = 0

    def __str__(self) -> str:
        return (
            f"products created={self.products_created} updated={self.products_updated} "
            f"skipped={self.products_skipped}; "
            f"variants created={self.variants_created} updated={self.variants_updated} "
            f"skipped={self.variants_skipped}"
        )


class MappingEngine:
    """In-memory matcher over the current supplier catalogue."""

    def __init__(self) -> None:
        self._supplier_variants = list(
            SupplierVariant.objects.select_related("product").prefetch_related("costs")
        )
        self._products = list(SupplierProduct.objects.all())
        self._by_sku: dict[str, SupplierVariant] = {}
        self._by_prefix: dict[str, list[SupplierVariant]] = defaultdict(list)
        self._by_product: dict[int, list[SupplierVariant]] = defaultdict(list)
        for variant in self._supplier_variants:
            if variant.sku:
                self._by_sku[variant.sku.strip().upper()] = variant
            tokens = sku_tokens(variant.sku)
            if tokens:
                self._by_prefix[tokens[0]].append(variant)
            self._by_product[variant.product_id].append(variant)

    def suggest_for_product(self, product: Product) -> ProductSuggestion:
        variants = list(product.variants.all())
        existing = getattr(product, "supplier_product_mapping", None)

        if existing and existing.is_confirmed and existing.no_supplier:
            return ProductSuggestion(
                product=product,
                no_supplier=True,
                confidence=MappingConfidence.CONFIRMED,
                reason=existing.match_reason or "Marked as having no supplier",
            )

        if existing and existing.is_confirmed and existing.supplier_product_id:
            variant_suggestions = [
                self.suggest_variant(variant, supplier_product=existing.supplier_product)
                for variant in variants
            ]
            return ProductSuggestion(
                product=product,
                supplier_product=existing.supplier_product,
                confidence=MappingConfidence.CONFIRMED,
                reason=existing.match_reason or "Confirmed blank",
                variant_suggestions=variant_suggestions,
            )

        if self._looks_digital(product, variants):
            return ProductSuggestion(
                product=product,
                no_supplier=True,
                confidence=MappingConfidence.SUGGESTED,
                reason="Looks like a digital product (no garment SKU / digital wording)",
            )

        blank, blank_reason = self._infer_blank(product, variants)
        prefix_unknown = (blank is None) and "no supplier catalogue yet" in blank_reason
        variant_suggestions = [
            self.suggest_variant(variant, supplier_product=blank) for variant in variants
        ]

        matched_products = [
            suggestion.supplier_variant.product
            for suggestion in variant_suggestions
            if suggestion.supplier_variant is not None
        ]
        # A shop SKU like AT002-* names a real blank we just do not have prices
        # for yet. Do not "helpfully" assign those variants to a different
        # garment that happens to share a size and colour.
        if matched_products and not prefix_unknown:
            majority, count = Counter(matched_products).most_common(1)[0]
            if blank is None or majority.pk == blank.pk:
                blank = majority
                blank_reason = blank_reason or f"majority of variant matches ({count})"

        confidence = MappingConfidence.SUGGESTED if blank or blank_reason else MappingConfidence.UNCERTAIN
        if blank and matched_products and any(item.pk != blank.pk for item in matched_products):
            confidence = MappingConfidence.UNCERTAIN
            blank_reason = (blank_reason + "; ").lstrip("; ") + "variant matches disagree on the blank"

        return ProductSuggestion(
            product=product,
            supplier_product=blank,
            confidence=confidence,
            reason=blank_reason,
            variant_suggestions=variant_suggestions,
        )

    def suggest_variant(
        self,
        variant: ProductVariant,
        *,
        supplier_product: SupplierProduct | None = None,
    ) -> VariantSuggestion:
        exact = self._match_exact_sku(variant)
        if exact:
            return VariantSuggestion(variant, exact, MappingConfidence.SUGGESTED, "exact SKU")

        token = self._match_sku_tokens(variant, supplier_product)
        if token:
            return VariantSuggestion(variant, token, MappingConfidence.SUGGESTED, "SKU token set")

        pool = self._candidate_pool(variant, supplier_product)
        match, reason = self._match_size_colour(variant, pool)
        if match:
            confidence = (
                MappingConfidence.SUGGESTED
                if supplier_product or sku_tokens(variant.sku)
                else MappingConfidence.UNCERTAIN
            )
            return VariantSuggestion(variant, match, confidence, reason)

        return VariantSuggestion(variant, None, MappingConfidence.UNCERTAIN, "no match")

    def _match_exact_sku(self, variant: ProductVariant) -> SupplierVariant | None:
        sku = (variant.sku or "").strip().upper()
        if not sku:
            return None
        return self._by_sku.get(sku)

    def _match_sku_tokens(
        self,
        variant: ProductVariant,
        supplier_product: SupplierProduct | None,
    ) -> SupplierVariant | None:
        tokens = sku_tokens(variant.sku)
        if not tokens:
            return None
        prefix, rest = tokens
        candidates = self._by_prefix.get(prefix, [])
        if supplier_product:
            candidates = [item for item in candidates if item.product_id == supplier_product.pk]
        matches = [
            item
            for item in candidates
            if (parsed := sku_tokens(item.sku)) and parsed[1] == rest
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _candidate_pool(
        self,
        variant: ProductVariant,
        supplier_product: SupplierProduct | None,
    ) -> list[SupplierVariant]:
        if supplier_product:
            return self._by_product.get(supplier_product.pk, [])
        tokens = sku_tokens(variant.sku)
        if tokens:
            # Unknown prefixes (AT002 before that blank is in the catalogue)
            # must not be matched against every other garment by size/colour.
            return self._by_prefix.get(tokens[0], [])
        return self._supplier_variants

    def _match_size_colour(
        self,
        variant: ProductVariant,
        candidates: list[SupplierVariant],
    ) -> tuple[SupplierVariant | None, str]:
        if not variant.size or not variant.colour:
            return None, ""
        matches = [
            item
            for item in candidates
            if sizes_match(variant.size, item.size) and colours_match(variant.colour, item.colour)
        ]
        if len(matches) == 1:
            return matches[0], "size and colour"
        if len(matches) > 1:
            tied = [item for item in matches if sku_token_match(variant.sku, item.sku)]
            if len(tied) == 1:
                return tied[0], "size, colour and SKU tokens"
            return None, f"{len(matches)} supplier variants match this size and colour"
        return None, ""

    def _infer_blank(
        self, product: Product, variants: list[ProductVariant]
    ) -> tuple[SupplierProduct | None, str]:
        prefixes = []
        for variant in variants:
            tokens = sku_tokens(variant.sku)
            if tokens:
                prefixes.append(tokens[0])
        if not prefixes:
            return None, ""

        prefix, count = Counter(prefixes).most_common(1)[0]
        if count < max(1, (len(prefixes) + 1) // 2):
            return None, ""

        for supplier_product in self._products:
            if (supplier_product.supplier_id or "").strip().upper() == prefix:
                return supplier_product, f"SKU prefix {prefix} matches supplier id"

        products = {item.product for item in self._by_prefix.get(prefix, [])}
        if len(products) == 1:
            return products.pop(), f"SKU prefix {prefix}"
        if len(products) > 1:
            ranked = Counter(item.product for item in self._by_prefix[prefix])
            winner, _ = ranked.most_common(1)[0]
            return winner, f"SKU prefix {prefix} (most common blank)"
        return None, f"SKU prefix {prefix} has no supplier catalogue yet"

    def _looks_digital(self, product: Product, variants: list[ProductVariant]) -> bool:
        product_type = (product.product_type or "").casefold()
        if product_type in _APPAREL_TYPES:
            return False
        if _text_has_hint(product.title, _PHYSICAL_TITLE_HINTS):
            return False
        if any(variant.size and variant.colour for variant in variants):
            return False
        skus = [variant.sku for variant in variants if variant.sku]
        if any(looks_like_blank_sku(sku) for sku in skus):
            return False

        haystack = " ".join(
            [
                product.title or "",
                product.product_type or "",
                " ".join(product.tags or []),
            ]
        )
        if _text_has_hint(haystack, _DIGITAL_HINTS):
            return True
        return not skus and not any(variant.size or variant.colour for variant in variants)


def apply_suggestions(
    *,
    products: QuerySet[Product] | None = None,
    product: Product | None = None,
) -> ApplyResult:
    """Write suggested mappings. Confirmed rows are left alone."""
    queryset = products if products is not None else Product.objects.all()
    if product is not None:
        queryset = queryset.filter(pk=product.pk)
    queryset = queryset.select_related("supplier_product_mapping").prefetch_related(
        "variants",
        "variants__supplier_mapping",
    )

    engine = MappingEngine()
    result = ApplyResult()
    for item in queryset:
        suggestion = engine.suggest_for_product(item)
        _apply_product_suggestion(item, suggestion, result)
        for variant_suggestion in suggestion.variant_suggestions:
            _apply_variant_suggestion(variant_suggestion, result)
    return result


def _apply_product_suggestion(
    product: Product, suggestion: ProductSuggestion, result: ApplyResult
) -> None:
    existing = getattr(product, "supplier_product_mapping", None)
    if existing and existing.is_confirmed:
        result.products_skipped += 1
        return
    if not suggestion.no_supplier and suggestion.supplier_product is None:
        if existing:
            existing.delete()
            result.products_updated += 1
        else:
            result.products_skipped += 1
        return

    defaults = {
        "supplier_product": None if suggestion.no_supplier else suggestion.supplier_product,
        "no_supplier": suggestion.no_supplier,
        "confidence": suggestion.confidence,
        "match_reason": suggestion.reason,
    }
    if existing:
        for field_name, value in defaults.items():
            setattr(existing, field_name, value)
        existing.save()
        result.products_updated += 1
        return
    ProductMapping.objects.create(product=product, **defaults)
    result.products_created += 1


def _apply_variant_suggestion(suggestion: VariantSuggestion, result: ApplyResult) -> None:
    if suggestion.supplier_variant is None:
        existing = getattr(suggestion.variant, "supplier_mapping", None)
        if existing and not existing.is_confirmed:
            existing.delete()
            result.variants_updated += 1
        else:
            result.variants_skipped += 1
        return
    existing = getattr(suggestion.variant, "supplier_mapping", None)
    if existing and existing.is_confirmed:
        result.variants_skipped += 1
        return
    if existing:
        existing.supplier_variant = suggestion.supplier_variant
        existing.confidence = suggestion.confidence
        existing.match_reason = suggestion.reason
        existing.save()
        result.variants_updated += 1
        return
    VariantMapping.objects.create(
        product_variant=suggestion.variant,
        supplier_variant=suggestion.supplier_variant,
        confidence=suggestion.confidence,
        match_reason=suggestion.reason,
    )
    result.variants_created += 1


def confirm_product_mapping(product: Product) -> ProductMapping | None:
    mapping = getattr(product, "supplier_product_mapping", None)
    if mapping is None:
        return None
    mapping.confidence = MappingConfidence.CONFIRMED
    mapping.save(update_fields=["confidence", "updated_at"])
    return mapping


def confirm_variant_mapping(variant: ProductVariant) -> VariantMapping | None:
    mapping = getattr(variant, "supplier_mapping", None)
    if mapping is None:
        return None
    mapping.confidence = MappingConfidence.CONFIRMED
    mapping.save(update_fields=["confidence", "updated_at"])
    return mapping


def confirm_all_for_product(product: Product) -> int:
    """Confirm the product mapping and every variant mapping that exists."""
    confirmed = 0
    if confirm_product_mapping(product):
        confirmed += 1
    for variant in product.variants.all():
        if confirm_variant_mapping(variant):
            confirmed += 1
    return confirmed


def set_product_blank(product: Product, supplier_product: SupplierProduct) -> ProductMapping:
    mapping, _ = ProductMapping.objects.update_or_create(
        product=product,
        defaults={
            "supplier_product": supplier_product,
            "no_supplier": False,
            "confidence": MappingConfidence.CONFIRMED,
            "match_reason": "Chosen in the mapping UI",
        },
    )
    return mapping


def mark_product_digital(product: Product) -> ProductMapping:
    mapping, _ = ProductMapping.objects.update_or_create(
        product=product,
        defaults={
            "supplier_product": None,
            "no_supplier": True,
            "confidence": MappingConfidence.CONFIRMED,
            "match_reason": "Marked as digital / no supplier",
        },
    )
    return mapping


def set_variant_mapping(
    variant: ProductVariant,
    supplier_variant: SupplierVariant,
    *,
    confidence: str = MappingConfidence.CONFIRMED,
    reason: str = "Chosen in the mapping UI",
) -> VariantMapping:
    mapping, _ = VariantMapping.objects.update_or_create(
        product_variant=variant,
        defaults={
            "supplier_variant": supplier_variant,
            "confidence": confidence,
            "match_reason": reason,
        },
    )
    return mapping


def create_supplier_blank(
    *,
    name: str,
    supplier_id: str = "",
    brand: str = "",
) -> SupplierProduct:
    return SupplierProduct.objects.create(
        name=name.strip(),
        supplier_id=supplier_id.strip(),
        brand=brand.strip(),
    )


def create_supplier_variant(
    *,
    product: SupplierProduct,
    sku: str = "",
    size: str = "",
    colour: str = "",
    unit_cost: Decimal | None = None,
    effective_from: date | None = None,
) -> SupplierVariant:
    variant = SupplierVariant.objects.create(
        product=product,
        sku=sku.strip(),
        size=normalise_size(size) if size else "",
        colour=colour.strip(),
    )
    if unit_cost is not None:
        # Default far enough back that historical orders pick it up. This is
        # "the first cost we know about", not "the price changed today".
        SupplierVariantCost.objects.create(
            supplier_variant=variant,
            unit_cost=unit_cost,
            effective_from=effective_from or date(2020, 1, 1),
            source="manual",
            note="Entered from the mapping UI",
        )
    return variant


def create_missing_variants_for_product(
    product: Product,
    *,
    unit_cost: Decimal,
    supplier_product: SupplierProduct | None = None,
) -> list[VariantMapping]:
    """Create a supplier variant (and mapping) for every unmapped shop variant.

    Used when the blank is known (e.g. AT002) but Inkthreadable never sent a
    catalogue row for that size/colour because it has not been ordered yet.
    """
    mapping = getattr(product, "supplier_product_mapping", None)
    blank = supplier_product or (mapping.supplier_product if mapping else None)
    if blank is None:
        raise ValueError("Assign a supplier blank before creating missing variants.")

    created: list[VariantMapping] = []
    for variant in product.variants.all():
        if getattr(variant, "supplier_mapping", None):
            continue
        supplier_variant = create_supplier_variant(
            product=blank,
            sku=variant.sku,
            size=variant.size,
            colour=variant.colour,
            unit_cost=unit_cost,
        )
        created.append(
            set_variant_mapping(
                variant,
                supplier_variant,
                confidence=MappingConfidence.SUGGESTED,
                reason="Created from shop variant (blank has no catalogue row yet)",
            )
        )
    return created


def parse_money(value: str) -> Decimal | None:
    """Parse a form amount. Empty string is None; invalid is None."""
    cleaned = (value or "").strip().replace("£", "")
    if not cleaned:
        return None
    return to_decimal(cleaned)
