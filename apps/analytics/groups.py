"""Named product groups (hoodies, tees, …) and how products join them.

Shopify product types are empty on this shop, so membership is filled from
title words as well as ``auto_match_product_types``. Groups only ever add
products — a manual removal in admin is not undone here.
"""

from __future__ import annotations

from django.utils.text import slugify

from apps.catalog.models import Product, ProductGroup

GROUP_SPECS = [
    {
        "name": "Hoodies",
        "types": "Hoodie",
        "needles": ("hoodie",),
        "sort_order": 10,
        "colour": "#4f46e5",
    },
    {
        "name": "Sweatshirts",
        "types": "Sweatshirt",
        "needles": ("sweatshirt",),
        "sort_order": 20,
        "colour": "#0f172a",
    },
    {
        "name": "T-Shirts",
        "types": "T-Shirt, Tee, Tshirt",
        "needles": ("t-shirt",),
        "sort_order": 30,
        "colour": "#818cf8",
    },
    {
        "name": "Headwear",
        "types": "Beanie, Cap, Snapback, Hat",
        "needles": ("beanie", "snapback", " baseball cap", " cap"),
        "sort_order": 40,
        "colour": "#b45309",
    },
    {
        "name": "Mugs",
        "types": "Mug",
        "needles": ("mug",),
        "sort_order": 50,
        "colour": "#15803d",
    },
    {
        "name": "Kids",
        "types": "Kids",
        "needles": ("kids", " kid "),
        "sort_order": 60,
        "colour": "#db2777",
    },
    {
        "name": "Digital downloads",
        "types": "Digital",
        "needles": ("bible verses", "bible promises"),
        "sort_order": 90,
        "colour": "#64748b",
    },
]

_APPAREL_NEEDLES = (
    "hoodie",
    "sweatshirt",
    "t-shirt",
    "tshirt",
    "beanie",
    "snapback",
    "mug",
    "tote",
    "notebook",
    "journal",
    " cap",
)


def ensure_default_groups() -> list[ProductGroup]:
    """Create the standard groups and add matching products. Safe to call often."""
    for spec in GROUP_SPECS:
        group, _ = ProductGroup.objects.get_or_create(
            slug=slugify(spec["name"]),
            defaults={
                "name": spec["name"],
                "auto_match_product_types": spec["types"],
                "sort_order": spec["sort_order"],
                "colour": spec["colour"],
            },
        )
        changed = False
        if not group.auto_match_product_types:
            group.auto_match_product_types = spec["types"]
            changed = True
        if group.sort_order == 100:
            group.sort_order = spec["sort_order"]
            changed = True
        if changed:
            group.save(update_fields=["auto_match_product_types", "sort_order"])
        group.apply_auto_match()
        _assign_by_title(group, spec["needles"])
        if spec["name"] == "Digital downloads":
            _drop_apparel_from_digital(group)
        if spec["name"] == "T-Shirts":
            _drop_titles_containing(group, ("sweatshirt",))
    return list(ProductGroup.objects.prefetch_related("products"))


def _assign_by_title(group: ProductGroup, needles: tuple[str, ...]) -> int:
    added = []
    for product in Product.objects.exclude(groups=group).only("id", "title"):
        title = f" {product.title.casefold()} "
        if any(needle in title for needle in needles):
            added.append(product)
    if added:
        group.products.add(*added)
    return len(added)


def ungrouped_products():
    return Product.objects.filter(groups__isnull=True)


def _drop_titles_containing(group: ProductGroup, needles: tuple[str, ...]) -> int:
    remove = [
        product
        for product in group.products.only("id", "title")
        if any(needle in product.title.casefold() for needle in needles)
    ]
    if remove:
        group.products.remove(*remove)
    return len(remove)


def _drop_apparel_from_digital(group: ProductGroup) -> int:
    """A 'Bible Verse T-shirt' is merch, not a lead magnet."""
    remove = [
        product
        for product in group.products.only("id", "title")
        if any(needle in f" {product.title.casefold()} " for needle in _APPAREL_NEEDLES)
    ]
    if remove:
        group.products.remove(*remove)
    return len(remove)
