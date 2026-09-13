"""What the shop sells.

These are *canonical* product records, not Shopify records. The Shopify adapter
fills them in, but the analytics layer only ever reads from here, so a second
sales channel could be added later without rewriting any calculations.

Two things in here exist purely to make the analytics answerable:

``ProductVariant.size`` / ``ProductVariant.colour``
    Shopify stores variant options positionally — ``option1``, ``option2`` —
    with names defined per product, so "which size sells best" is unanswerable
    without normalising them first. A product might call the option "Size" while
    another calls it "Shirt Size", and the values range over "L", "Large" and
    "lg". These fields hold the normalised answer alongside the raw options.

``ProductGroup``
    Lets products be analysed in sets ("T-Shirts", "Hoodies") without hard-coding
    a category list, since what counts as a useful grouping changes over time.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.core.models import MoneyField, TimeStampedModel
from apps.core.money import ZERO

# Canonical size codes in the order a human expects to read them on a chart.
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]

# Everything a size option might be written as, mapped to its canonical code.
_SIZE_ALIASES = {
    "xxs": "XXS",
    "xs": "XS",
    "extra small": "XS",
    "x-small": "XS",
    "s": "S",
    "sm": "S",
    "small": "S",
    "m": "M",
    "md": "M",
    "medium": "M",
    "l": "L",
    "lg": "L",
    "large": "L",
    "xl": "XL",
    "x-large": "XL",
    "extra large": "XL",
    "xxl": "2XL",
    "2xl": "2XL",
    "2x": "2XL",
    "2x-large": "2XL",
    "2xlarge": "2XL",
    "xx-large": "2XL",
    "xxxl": "3XL",
    "3xl": "3XL",
    "3x": "3XL",
    "xxxxl": "4XL",
    "4xl": "4XL",
    "5xl": "5XL",
}

# Option names that mean "size" or "colour", however the product spells them.
_SIZE_OPTION_NAMES = ("size", "sizes", "shirt size", "garment size", "fit")
_COLOUR_OPTION_NAMES = ("colour", "color", "colours", "colors", "shade", "garment colour")


def normalise_size(value: str) -> str:
    """Return the canonical code for a size, or the input cleaned up if unknown.

    Unknown values are preserved rather than discarded, so an unusual option
    still groups with itself and shows up in analytics instead of vanishing.
    """
    cleaned = " ".join((value or "").split())
    if not cleaned:
        return ""
    return _SIZE_ALIASES.get(cleaned.casefold(), cleaned)


def normalise_colour(value: str) -> str:
    """Title-case a colour so "black", "Black" and "BLACK" group together."""
    cleaned = " ".join((value or "").split())
    return cleaned.title() if cleaned else ""


def size_sort_key(size: str) -> tuple[int, str]:
    """Sort canonical sizes smallest-first, with unknown sizes last."""
    try:
        return (SIZE_ORDER.index(size), "")
    except ValueError:
        return (len(SIZE_ORDER), size)


class ProductStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DRAFT = "draft", "Draft"
    ARCHIVED = "archived", "Archived"


class ProductGroup(TimeStampedModel):
    """A named set of products that should be analysed together.

    Membership is explicit, but ``auto_match_product_types`` lets a group adopt
    new products automatically as they are added to the shop, so a group does
    not silently go stale after a sync.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    colour = models.CharField(
        max_length=7,
        default="#6366f1",
        help_text="Hex colour used for this group in charts",
    )
    sort_order = models.IntegerField(default=100)

    products = models.ManyToManyField(
        "catalog.Product",
        related_name="groups",
        blank=True,
    )
    auto_match_product_types = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "Comma-separated Shopify product types that should join this group "
            "automatically, e.g. 'T-Shirt, Tee'. Case-insensitive."
        ),
    )

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def matched_product_types(self) -> list[str]:
        return [part.strip() for part in self.auto_match_product_types.split(",") if part.strip()]

    def apply_auto_match(self) -> int:
        """Add any product whose type matches this group. Returns rows added.

        Only ever adds. Manually removing a product from a group would otherwise
        be undone by the next sync, which would be infuriating.
        """
        types = self.matched_product_types
        if not types:
            return 0
        condition = Q()
        for product_type in types:
            condition |= Q(product_type__iexact=product_type)
        candidates = Product.objects.filter(condition).exclude(groups=self)
        added = list(candidates)
        if added:
            self.products.add(*added)
        return len(added)


class Product(TimeStampedModel):
    """A product as sold, independent of which channel it came from."""

    shopify_id = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Shopify's global ID. Kept so syncing never duplicates a product.",
    )
    title = models.CharField(max_length=300)
    handle = models.SlugField(max_length=300, blank=True)
    product_type = models.CharField(max_length=120, blank=True, db_index=True)
    vendor = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ProductStatus.choices,
        default=ProductStatus.ACTIVE,
        db_index=True,
    )
    tags = models.JSONField(default=list, blank=True)

    published_at = models.DateTimeField(null=True, blank=True)
    shopify_created_at = models.DateTimeField(null=True, blank=True)
    shopify_updated_at = models.DateTimeField(null=True, blank=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["product_type", "status"])]

    def __str__(self) -> str:
        return self.title

    @property
    def variant_count(self) -> int:
        return self.variants.count()

    @property
    def price_range(self) -> tuple[object, object] | None:
        """Lowest and highest variant price, or None when there are no variants."""
        prices = [v.price for v in self.variants.all() if v.price is not None]
        return (min(prices), max(prices)) if prices else None


class ProductVariant(TimeStampedModel):
    """A specific buyable configuration of a product.

    This is the level at which profit is genuinely knowable, because supplier
    cost varies by size and colour.
    """

    shopify_id = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    title = models.CharField(
        max_length=300,
        blank=True,
        help_text="Shopify's variant title, e.g. 'Black / L'",
    )
    sku = models.CharField(max_length=120, blank=True, db_index=True)
    barcode = models.CharField(max_length=120, blank=True)
    position = models.IntegerField(default=1)

    price = MoneyField(
        null=True,
        blank=True,
        help_text="Current list price. Historical orders keep their own price.",
    )
    compare_at_price = MoneyField(null=True, blank=True)

    options = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw option names and values as Shopify supplied them",
    )
    size = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
        help_text="Normalised size code, derived from options",
    )
    colour = models.CharField(
        max_length=60,
        blank=True,
        db_index=True,
        help_text="Normalised colour name, derived from options",
    )

    inventory_quantity = models.IntegerField(null=True, blank=True)
    shopify_unit_cost = MoneyField(
        null=True,
        blank=True,
        help_text="Shopify Cost per item, if one was entered on the variant. Used only when no supplier cost exists.",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["product__title", "position"]
        indexes = [
            models.Index(fields=["product", "size"]),
            models.Index(fields=["product", "colour"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.title} — {self.title or self.sku or self.pk}"

    @property
    def display_options(self) -> str:
        """Size and colour if known, else whatever Shopify called the variant."""
        parts = [p for p in (self.colour, self.size) if p]
        return " / ".join(parts) or self.title

    def derive_options(self) -> None:
        """Fill ``size`` and ``colour`` from ``options``.

        Called by the sync rather than by ``save``, so that a manual correction
        in the admin is not immediately overwritten.
        """
        for name, value in (self.options or {}).items():
            key = (name or "").strip().casefold()
            if key in _SIZE_OPTION_NAMES:
                self.size = normalise_size(str(value))
            elif key in _COLOUR_OPTION_NAMES:
                self.colour = normalise_colour(str(value))

    @property
    def supplier_variant(self):
        """The mapped Inkthreadable variant, or None if not yet mapped."""
        mapping = getattr(self, "supplier_mapping", None)
        return mapping.supplier_variant if mapping else None

    def cost_on(self, when):
        """Supplier cost that applied on a given date, or None if unknown.

        Returns None rather than zero when unmapped, because a missing cost and a
        free product are very different things and must not be conflated in a
        profit figure. A product explicitly marked as having no supplier
        (digital downloads) is the exception: that cost is genuinely zero.
        """
        mapping = getattr(self, "supplier_mapping", None)
        if mapping:
            return mapping.cost_on(when)
        product_mapping = getattr(self.product, "supplier_product_mapping", None)
        if product_mapping and product_mapping.no_supplier:
            return ZERO
        return None
