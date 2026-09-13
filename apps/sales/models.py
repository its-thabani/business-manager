"""Orders, order lines, customers and refunds.

Sign convention in this module differs deliberately from ``apps.finance``. Bank
transactions are signed (money in positive, money out negative) because that is
what a bank statement means. Order figures are instead stored as **positive
magnitudes with explicit names** — ``total_discounts`` is what was discounted,
``shipping_charged`` is what the customer paid for postage — because that is how
Shopify presents them and how an invoice reads. The arithmetic that turns these
into revenue and profit lives in ``apps.analytics.profitability``, in one place,
rather than being implied by signs scattered across the schema.

Three concepts are kept firmly apart, because conflating them is what makes
spreadsheets lie:

Revenue
    What was sold, on the day it was sold.
Profit
    Revenue less the costs attributable to it.
Cash received
    What actually landed in Monzo, days later, net of fees and refunds.

An order knows about the first two. The third lives in ``apps.finance`` and the
two are joined up by the reconciliation layer, never by assumption.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import F, Q, QuerySet, Sum

from apps.core.models import MoneyField, QuantityField, TimeStampedModel


class FinancialStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    AUTHORIZED = "authorized", "Authorised"
    PARTIALLY_PAID = "partially_paid", "Partially paid"
    PAID = "paid", "Paid"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"
    VOIDED = "voided", "Voided"
    UNKNOWN = "unknown", "Unknown"


class FulfilmentStatus(models.TextChoices):
    UNFULFILLED = "unfulfilled", "Unfulfilled"
    PARTIAL = "partial", "Partially fulfilled"
    FULFILLED = "fulfilled", "Fulfilled"
    RESTOCKED = "restocked", "Restocked"
    UNKNOWN = "unknown", "Unknown"


class Customer(TimeStampedModel):
    """Someone who has ordered.

    Counts and totals that Shopify supplies are stored for cross-checking, but
    every customer metric the app reports is computed from our own order rows, so
    the numbers stay consistent with everything else.
    """

    shopify_id = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    email = models.EmailField(blank=True, db_index=True)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=2, blank=True, db_index=True)

    accepts_marketing = models.BooleanField(default=False)
    shopify_orders_count = models.IntegerField(null=True, blank=True)
    shopify_total_spent = MoneyField(null=True, blank=True)
    shopify_created_at = models.DateTimeField(null=True, blank=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name", "email"]

    def __str__(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name or self.email or f"Customer {self.pk}"

    @property
    def display_name(self) -> str:
        return str(self)


class OrderQuerySet(models.QuerySet):
    """Filters that encode what "a real sale" means, in one place."""

    def countable(self) -> OrderQuerySet:
        """Orders that should appear in business figures.

        Test orders and cancelled orders are excluded: including them silently
        inflates every metric, and the exclusion is easy to forget if it is not
        expressed once and reused.
        """
        return self.filter(is_test=False, cancelled_at__isnull=True)

    def in_range(self, date_range) -> OrderQuerySet:
        """Orders placed within an inclusive date range, by local calendar day."""
        return self.filter(placed_on__gte=date_range.start, placed_on__lte=date_range.end)

    def for_product(self, product) -> OrderQuerySet:
        return self.filter(lines__product=product).distinct()

    def for_group(self, group) -> OrderQuerySet:
        return self.filter(lines__product__groups=group).distinct()


class Order(TimeStampedModel):
    """A Shopify order.

    ``placed_on`` duplicates the date part of ``placed_at`` on purpose. Grouping
    by a date extracted from a timestamp forces the database to convert every row
    to local time before it can use an index, which turns every "profit by month"
    query into a table scan. Storing the local calendar day once, at sync time,
    keeps date filtering indexable and keeps the day boundaries correct for a UK
    business regardless of how the timestamp was stored.
    """

    shopify_id = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    name = models.CharField(
        max_length=50,
        db_index=True,
        help_text="The order name as shown to the customer, e.g. '#1042'",
    )
    # Shopify's numeric id is 64-bit (e.g. 12026310427001). Postgres integer
    # is 32-bit and rejects those values; SQLite does not, so this only
    # surfaced when loading the books into Neon.
    order_number = models.BigIntegerField(null=True, blank=True, db_index=True)

    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    email = models.EmailField(blank=True)

    placed_at = models.DateTimeField(db_index=True)
    placed_on = models.DateField(
        db_index=True,
        help_text="Local calendar day of placed_at, stored so date grouping stays indexable",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=100, blank=True)

    currency = models.CharField(max_length=3, default="GBP")
    financial_status = models.CharField(
        max_length=30,
        choices=FinancialStatus.choices,
        default=FinancialStatus.UNKNOWN,
        db_index=True,
    )
    fulfilment_status = models.CharField(
        max_length=30,
        choices=FulfilmentStatus.choices,
        default=FulfilmentStatus.UNKNOWN,
        db_index=True,
    )

    # --- Money, all positive magnitudes -----------------------------------
    total_discounts = MoneyField(
        default=Decimal("0.00"),
        help_text="Total discounted across the order, as a positive amount",
    )
    shipping_charged = MoneyField(
        default=Decimal("0.00"),
        help_text="What the customer paid for postage",
    )
    total_tax = MoneyField(default=Decimal("0.00"))
    total_price = MoneyField(
        default=Decimal("0.00"),
        help_text="What the customer was charged in total",
    )
    payment_fee = MoneyField(
        null=True,
        blank=True,
        help_text=(
            "Actual processing fee, when the payment provider reports it. Null "
            "means unknown, and profit will fall back to an estimate and say so."
        ),
    )

    is_test = models.BooleanField(default=False, db_index=True)
    source_name = models.CharField(max_length=60, blank=True)
    shipping_country = models.CharField(max_length=2, blank=True, db_index=True)
    discount_codes = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)

    raw = models.JSONField(
        default=dict,
        blank=True,
        help_text="Shopify's payload as received, so nothing is lost in translation",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ["-placed_at"]
        indexes = [
            models.Index(fields=["placed_on", "is_test"]),
            models.Index(fields=["-placed_at"]),
        ]

    def __str__(self) -> str:
        return self.name or f"Order {self.pk}"

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    @property
    def is_countable(self) -> bool:
        return not self.is_test and not self.is_cancelled

    @property
    def gross_line_revenue(self) -> Decimal:
        """Sum of line prices before discount, computed from the lines."""
        total = self.lines.aggregate(
            total=Sum(F("unit_price") * F("quantity"), output_field=MoneyField())
        )["total"]
        return total or Decimal("0.00")

    @property
    def units(self) -> int:
        return self.lines.aggregate(n=Sum("quantity"))["n"] or 0

    @property
    def refunded_amount(self) -> Decimal:
        """Everything refunded against this order, as a positive amount."""
        return self.refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


class OrderLine(TimeStampedModel):
    """One product variant within an order.

    ``product`` and ``variant`` are nullable and the descriptive fields are
    snapshots, because a variant can be deleted from Shopify long after it was
    sold. The order must still report what was bought and for how much.
    """

    shopify_id = models.CharField(max_length=64, blank=True, db_index=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")

    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lines",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lines",
        help_text="Denormalised from the variant so product analytics stay fast",
    )

    title = models.CharField(max_length=300, help_text="Product title as sold")
    variant_title = models.CharField(max_length=300, blank=True)
    sku = models.CharField(max_length=120, blank=True, db_index=True)

    quantity = models.IntegerField(default=1)
    unit_price = MoneyField(help_text="Price per unit before discount")
    discount_amount = MoneyField(
        default=Decimal("0.00"),
        help_text="Discount allocated to this line, as a positive amount",
    )
    tax_amount = MoneyField(default=Decimal("0.00"))

    unit_cost_override = MoneyField(
        null=True,
        blank=True,
        help_text=(
            "Actual unit cost for this line, if known. Overrides the supplier "
            "price list, which is only ever an estimate of what was really paid."
        ),
    )
    requires_shipping = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "pk"]
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["variant"]),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.title}"

    @property
    def gross_revenue(self) -> Decimal:
        return (self.unit_price or Decimal("0.00")) * self.quantity

    @property
    def net_revenue(self) -> Decimal:
        """Line revenue after its share of discount, before tax and refunds."""
        return self.gross_revenue - (self.discount_amount or Decimal("0.00"))

    @property
    def refunded_quantity(self) -> Decimal:
        return self.refund_lines.aggregate(n=Sum("quantity"))["n"] or Decimal("0")

    @property
    def refunded_amount(self) -> Decimal:
        return self.refund_lines.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    @property
    def net_quantity(self) -> Decimal:
        """Units sold and kept, i.e. after refunds."""
        return Decimal(self.quantity) - self.refunded_quantity


class Refund(TimeStampedModel):
    """Money given back to a customer.

    Refunds are separate rows rather than an adjustment to the order, so that
    "what did this order look like at the time" and "what did it end up being
    worth" are both answerable.
    """

    shopify_id = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="refunds")

    refunded_at = models.DateTimeField(db_index=True)
    refunded_on = models.DateField(
        db_index=True,
        help_text="Local calendar day of refunded_at",
    )
    amount = MoneyField(help_text="Total refunded, as a positive amount")
    shipping_refunded = MoneyField(default=Decimal("0.00"))
    tax_refunded = MoneyField(default=Decimal("0.00"))
    note = models.TextField(blank=True)
    restocked = models.BooleanField(
        default=False,
        help_text="Whether stock came back, which affects whether the cost is recovered",
    )

    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-refunded_at"]

    def __str__(self) -> str:
        return f"Refund of £{self.amount} on {self.order}"


class RefundLine(TimeStampedModel):
    """The part of a refund attributable to a specific order line.

    Needed for per-variant refund rates: an order-level refund total cannot tell
    you that it is always the white small that comes back.
    """

    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(
        OrderLine,
        on_delete=models.CASCADE,
        related_name="refund_lines",
    )
    quantity = QuantityField(
        default=Decimal("0"),
        help_text="Units refunded. Fractional to allow partial value refunds.",
    )
    amount = MoneyField(help_text="Amount refunded for this line, as a positive amount")

    class Meta:
        ordering = ["refund", "pk"]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.order_line.title} refunded"
