"""Inkthreadable: what it costs to make and ship what the shop sells.

The central problem this module solves is that **supplier costs change over
time**. If a blank tee went from £8.10 to £9.35 in June, then an order from March
must still be costed at £8.10, or every historical margin silently rewrites
itself the next time a price list is synced — and "my margins fell" becomes
impossible to distinguish from "my cost data was overwritten".

So costs are stored as effective-dated rows in ``SupplierVariantCost`` and always
resolved as-of the order date. Nothing overwrites a cost; a change appends.

The other job here is mapping. Shopify and Inkthreadable name things differently
("StayLit Classic Tee / Black / Large" against "Stanley/Stella Creator 2.0 /
Black / L"), so ``VariantMapping`` records the link. Automatic suggestions are
allowed, but a human confirmation is recorded separately, because a wrong
mapping produces a confident and completely wrong profit figure.
"""

from __future__ import annotations

from datetime import date

from django.db import models

from apps.core.models import MoneyField, TimeStampedModel


class SupplierProduct(TimeStampedModel):
    """A blank product in the supplier's catalogue."""

    supplier_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Inkthreadable's identifier, when the API provides one",
    )
    name = models.CharField(max_length=300)
    brand = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
        help_text="e.g. 'Stanley/Stella', 'Gildan'",
    )
    category = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)
    is_discontinued = models.BooleanField(
        default=False,
        help_text=(
            "Inkthreadable has phased this blank out. Historical costs stay; "
            "new orders cannot assume it is still available."
        ),
    )

    class Meta:
        ordering = ["brand", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier_id"],
                condition=models.Q(supplier_id__gt=""),
                name="unique_supplier_product_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.brand} {self.name}".strip() if self.brand else self.name


class SupplierVariant(TimeStampedModel):
    """A specific size and colour of a supplier product."""

    supplier_id = models.CharField(max_length=64, blank=True, db_index=True)
    product = models.ForeignKey(
        SupplierProduct,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    sku = models.CharField(max_length=120, blank=True, db_index=True)
    size = models.CharField(max_length=40, blank=True, db_index=True)
    colour = models.CharField(max_length=60, blank=True, db_index=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["product__name", "colour", "size"]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier_id"],
                condition=models.Q(supplier_id__gt=""),
                name="unique_supplier_variant_id",
            )
        ]

    def __str__(self) -> str:
        options = " / ".join(p for p in (self.colour, self.size) if p)
        return f"{self.product} — {options}" if options else str(self.product)

    def cost_on(self, when: date) -> MoneyField | None:
        """The print cost effective on ``when``, or None if no cost is recorded."""
        row = (
            self.costs.filter(effective_from__lte=when)
            .order_by("-effective_from", "-pk")
            .first()
        )
        return row.unit_cost if row else None

    @property
    def current_cost(self):
        from django.utils import timezone

        return self.cost_on(timezone.localdate())


class SupplierVariantCost(TimeStampedModel):
    """What a supplier variant cost from a given date onwards.

    Append-only by convention: when a price changes, add a row. The history is
    what makes "costs rose while price stayed flat" a provable statement rather
    than a guess.
    """

    supplier_variant = models.ForeignKey(
        SupplierVariant,
        on_delete=models.CASCADE,
        related_name="costs",
    )
    unit_cost = MoneyField(help_text="Print and blank cost per unit, excluding shipping")
    effective_from = models.DateField(
        db_index=True,
        help_text="First date this cost applied. Orders before this use the previous cost.",
    )
    source = models.CharField(
        max_length=20,
        choices=[
            ("api", "Supplier API"),
            ("manual", "Entered manually"),
            ("invoice", "Read from an invoice"),
        ],
        default="manual",
    )
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier_variant", "effective_from"],
                name="unique_cost_per_variant_per_date",
            )
        ]

    def __str__(self) -> str:
        return f"{self.supplier_variant} — £{self.unit_cost} from {self.effective_from}"


class MappingConfidence(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed by me"
    SUGGESTED = "suggested", "Suggested automatically"
    UNCERTAIN = "uncertain", "Uncertain — needs review"


class ProductMapping(TimeStampedModel):
    """Which Inkthreadable blank a Shopify product is printed on.

    Variant-level mappings still do the real costing. This row is the product
    decision: "this hoodie is an AWDis JH001" or "this is a digital download
    and has no supplier". New variants inherit from it.
    """

    product = models.OneToOneField(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="supplier_product_mapping",
    )
    supplier_product = models.ForeignKey(
        SupplierProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_mappings",
    )
    no_supplier = models.BooleanField(
        default=False,
        help_text="Digital or otherwise not fulfilled by Inkthreadable. Cost is zero.",
    )
    confidence = models.CharField(
        max_length=20,
        choices=MappingConfidence.choices,
        default=MappingConfidence.SUGGESTED,
        db_index=True,
    )
    match_reason = models.CharField(max_length=300, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["product__title"]

    def __str__(self) -> str:
        if self.no_supplier:
            return f"{self.product} — no supplier"
        return f"{self.product} → {self.supplier_product or 'unassigned'}"

    @property
    def is_confirmed(self) -> bool:
        return self.confidence == MappingConfidence.CONFIRMED


class VariantMapping(TimeStampedModel):
    """Links a Shopify variant to the supplier variant that fulfils it.

    Without this link the app cannot know what an order cost, so the count of
    unmapped variants is a headline number on the Data Health page.
    """

    product_variant = models.OneToOneField(
        "catalog.ProductVariant",
        on_delete=models.CASCADE,
        related_name="supplier_mapping",
    )
    supplier_variant = models.ForeignKey(
        SupplierVariant,
        on_delete=models.PROTECT,
        related_name="mappings",
    )
    confidence = models.CharField(
        max_length=20,
        choices=MappingConfidence.choices,
        default=MappingConfidence.SUGGESTED,
        db_index=True,
    )
    cost_override = MoneyField(
        null=True,
        blank=True,
        help_text=(
            "Use this unit cost instead of the supplier's price list. For when you "
            "know the real cost and the catalogue is wrong or missing."
        ),
    )
    match_reason = models.CharField(
        max_length=300,
        blank=True,
        help_text="How this mapping was arrived at, for auditing automatic matches",
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["product_variant__product__title", "product_variant__position"]

    def __str__(self) -> str:
        return f"{self.product_variant} → {self.supplier_variant}"

    @property
    def is_confirmed(self) -> bool:
        return self.confidence == MappingConfidence.CONFIRMED

    def cost_on(self, when: date):
        """Unit cost for this mapping on ``when``.

        An override wins outright and applies to all dates, since it represents
        knowledge the price list does not have.
        """
        if self.cost_override is not None:
            return self.cost_override
        return self.supplier_variant.cost_on(when)


class SupplierOrderStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PRODUCTION = "in_production", "In production"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    UNKNOWN = "unknown", "Unknown"


class SupplierOrder(TimeStampedModel):
    """A fulfilment order placed with Inkthreadable.

    This is the *actual* cost of fulfilling a Shopify order, as opposed to the
    catalogue estimate. Where one exists and is linked, order profitability uses
    it in preference to the price list, and says so.
    """

    supplier_reference = models.CharField(
        max_length=100,
        unique=True,
        help_text="Inkthreadable's order reference",
    )
    order = models.ForeignKey(
        "sales.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_orders",
        help_text="The Shopify order this fulfils, once matched",
    )
    status = models.CharField(
        max_length=20,
        choices=SupplierOrderStatus.choices,
        default=SupplierOrderStatus.UNKNOWN,
        db_index=True,
    )
    placed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    shipped_at = models.DateTimeField(null=True, blank=True)

    product_cost = MoneyField(
        null=True,
        blank=True,
        help_text="Total charged for the items, as a positive amount",
    )
    shipping_cost = MoneyField(
        null=True,
        blank=True,
        help_text="Total charged for postage, as a positive amount",
    )
    tax = MoneyField(null=True, blank=True)
    total_cost = MoneyField(null=True, blank=True)

    tracking_number = models.CharField(max_length=120, blank=True)
    tracking_url = models.URLField(blank=True)
    shipping_country = models.CharField(max_length=2, blank=True)

    raw = models.JSONField(
        default=dict,
        blank=True,
        help_text="The supplier's response as received, kept for auditing",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-placed_at"]

    def __str__(self) -> str:
        return f"Supplier order {self.supplier_reference}"

    @property
    def known_total(self):
        """Total cost, computed from parts if the supplier did not give a total."""
        if self.total_cost is not None:
            return self.total_cost
        parts = [p for p in (self.product_cost, self.shipping_cost, self.tax) if p is not None]
        return sum(parts) if parts else None
