"""Suggest Shopify → Inkthreadable mappings from SKU, size and colour."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import Product
from apps.supplier.mapping import apply_suggestions


class Command(BaseCommand):
    help = (
        "Suggest product and variant mappings from SKU / size / colour. "
        "Does not overwrite mappings you have confirmed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--product",
            type=int,
            help="Only suggest for this product id.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be written without saving.",
        )

    def handle(self, *args, **options):
        product = None
        if options["product"]:
            try:
                product = Product.objects.get(pk=options["product"])
            except Product.DoesNotExist as exc:
                raise CommandError(f"No product with id {options['product']}") from exc

        if options["dry_run"]:
            from apps.supplier.mapping import MappingEngine

            queryset = Product.objects.all()
            if product is not None:
                queryset = queryset.filter(pk=product.pk)
            engine = MappingEngine()
            blanks = digital = variants = 0
            for item in queryset.prefetch_related("variants"):
                suggestion = engine.suggest_for_product(item)
                if suggestion.no_supplier:
                    digital += 1
                elif suggestion.supplier_product:
                    blanks += 1
                variants += sum(1 for row in suggestion.variant_suggestions if row.supplier_variant)
            self.stdout.write(
                f"dry-run: {blanks} products would get a blank, "
                f"{digital} look digital, {variants} variants would match"
            )
            return

        result = apply_suggestions(product=product)
        self.stdout.write(str(result))
