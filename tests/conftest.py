from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product, ProductVariant
from apps.finance.models import BankAccount, BankTransaction, Category, TransactionSource
from apps.finance.seed import seed_all
from apps.sales.models import (
    Customer,
    FinancialStatus,
    Order,
    OrderLine,
    Refund,
    RefundLine,
)
from apps.supplier.models import (
    MappingConfidence,
    SupplierProduct,
    SupplierVariant,
    SupplierVariantCost,
    VariantMapping,
)


@pytest.fixture
def seeded(db):
    """A database with the standard categories and rules in place."""
    return seed_all()


@pytest.fixture
def account(db) -> BankAccount:
    return BankAccount.objects.create(name="Test Account", institution="Monzo", is_primary=True)


@pytest.fixture
def make_txn(account):
    """Create a transaction with sensible defaults."""

    def _make(
        amount: str | Decimal,
        *,
        counterparty: str = "",
        description: str = "",
        notes: str = "",
        when: date | None = None,
        category: Category | None = None,
        source: str = TransactionSource.MONZO_CSV,
        external_id: str = "",
        **kwargs,
    ) -> BankTransaction:
        return BankTransaction.objects.create(
            account=account,
            source=source,
            external_id=external_id,
            occurred_on=when or date(2026, 6, 15),
            counterparty=counterparty,
            description=description,
            notes=notes,
            amount=Decimal(amount),
            category=category,
            **kwargs,
        )

    return _make


@pytest.fixture
def category_by_name(seeded):
    def _get(name: str) -> Category:
        return Category.objects.get(name=name)

    return _get


# ---------------------------------------------------------------------------
# Catalogue, supplier and sales fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_product(db):
    """A product with variants, created from a compact spec.

    ``variants`` is a list of ``(colour, size, price)`` so a test can set up a
    realistic product in one line without obscuring what it is testing.
    """

    def _make(
        title: str = "StayLit Classic Tee",
        *,
        product_type: str = "T-Shirt",
        variants: list[tuple[str, str, str]] | None = None,
    ) -> Product:
        product = Product.objects.create(
            title=title,
            product_type=product_type,
            handle=title.lower().replace(" ", "-"),
            shopify_id=f"gid://shopify/Product/{Product.objects.count() + 1}",
        )
        for position, (colour, size, price) in enumerate(
            variants or [("Black", "L", "24.99")], start=1
        ):
            variant = ProductVariant(
                product=product,
                position=position,
                title=f"{colour} / {size}",
                sku=f"{product.handle}-{colour}-{size}".upper(),
                price=Decimal(price),
                options={"Colour": colour, "Size": size},
                shopify_id=f"gid://shopify/ProductVariant/{ProductVariant.objects.count() + position}",
            )
            variant.derive_options()
            variant.save()
        return product

    return _make


@pytest.fixture
def map_variant(db):
    """Map a product variant to a supplier variant at a given cost.

    ``costs`` is a list of ``(effective_from, unit_cost)`` so tests can exercise
    the effective-dated lookup that keeps historical margins stable.
    """

    def _map(
        variant: ProductVariant,
        *,
        costs: list[tuple[date, str]] | None = None,
        cost: str | None = "8.10",
        confidence: str = MappingConfidence.CONFIRMED,
        cost_override: str | None = None,
    ) -> VariantMapping:
        supplier_product, _ = SupplierProduct.objects.get_or_create(
            name="Creator 2.0",
            brand="Stanley/Stella",
        )
        supplier_variant = SupplierVariant.objects.create(
            product=supplier_product,
            colour=variant.colour,
            size=variant.size,
            sku=f"SS-{variant.colour}-{variant.size}".upper(),
        )
        for effective_from, unit_cost in costs or ([(date(2020, 1, 1), cost)] if cost else []):
            SupplierVariantCost.objects.create(
                supplier_variant=supplier_variant,
                unit_cost=Decimal(unit_cost),
                effective_from=effective_from,
            )
        return VariantMapping.objects.create(
            product_variant=variant,
            supplier_variant=supplier_variant,
            confidence=confidence,
            cost_override=Decimal(cost_override) if cost_override else None,
        )

    return _map


@pytest.fixture
def make_order(db):
    """Create an order with lines.

    ``lines`` is a list of ``(variant, quantity, unit_price)``. Order totals are
    computed to be internally consistent so tests exercise realistic data rather
    than figures that could never come out of Shopify.
    """

    def _make(
        *,
        lines: list[tuple[ProductVariant, int, str]],
        when: date = date(2026, 6, 15),
        shipping_charged: str = "3.95",
        discounts: str = "0.00",
        tax: str = "0.00",
        payment_fee: str | None = None,
        is_test: bool = False,
        cancelled: bool = False,
        name: str | None = None,
        customer: Customer | None = None,
        email: str = "",
        shipping_country: str = "",
        discount_codes: list | None = None,
    ) -> Order:
        placed_at = timezone.make_aware(datetime.combine(when, time(12, 0)))
        gross = sum(Decimal(price) * qty for _, qty, price in lines)
        total_price = gross - Decimal(discounts) + Decimal(shipping_charged) + Decimal(tax)
        order = Order.objects.create(
            shopify_id=f"gid://shopify/Order/{Order.objects.count() + 1}",
            name=name or f"#{1000 + Order.objects.count() + 1}",
            order_number=1000 + Order.objects.count() + 1,
            placed_at=placed_at,
            placed_on=when,
            cancelled_at=placed_at if cancelled else None,
            shipping_charged=Decimal(shipping_charged),
            total_discounts=Decimal(discounts),
            total_tax=Decimal(tax),
            total_price=total_price,
            payment_fee=Decimal(payment_fee) if payment_fee is not None else None,
            is_test=is_test,
            financial_status=FinancialStatus.PAID,
            customer=customer,
            email=email or (customer.email if customer else ""),
            shipping_country=shipping_country,
            discount_codes=discount_codes or [],
        )
        # Spread any order-level discount across lines in proportion to value, the
        # way Shopify allocates it, so per-line figures stay consistent.
        for variant, quantity, price in lines:
            line_gross = Decimal(price) * quantity
            allocated = (
                (Decimal(discounts) * line_gross / gross).quantize(Decimal("0.01"))
                if gross and Decimal(discounts)
                else Decimal("0.00")
            )
            OrderLine.objects.create(
                order=order,
                variant=variant,
                product=variant.product,
                title=variant.product.title,
                variant_title=variant.title,
                sku=variant.sku,
                quantity=quantity,
                unit_price=Decimal(price),
                discount_amount=allocated,
            )
        return order

    return _make


@pytest.fixture
def refund_order(db):
    """Refund some or all of an order line."""

    def _refund(
        order: Order,
        *,
        line: OrderLine | None = None,
        amount: str,
        quantity: str = "1",
        when: date | None = None,
    ) -> Refund:
        refund_on = when or order.placed_on
        refund = Refund.objects.create(
            order=order,
            refunded_at=timezone.make_aware(datetime.combine(refund_on, time(9, 0))),
            refunded_on=refund_on,
            amount=Decimal(amount),
        )
        target = line or order.lines.first()
        if target is not None:
            RefundLine.objects.create(
                refund=refund,
                order_line=target,
                quantity=Decimal(quantity),
                amount=Decimal(amount),
            )
        return refund

    return _refund
