"""Suggest bank links for Shopify sales days and supplier invoices."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.analytics.reconciliation import apply_suggestions, reconcile
from apps.core.periods import preset_range


class Command(BaseCommand):
    help = (
        "Suggest Shopify-day and supplier-invoice links to bank rows. "
        "Does not overwrite links you have confirmed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would match without saving.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            report = reconcile(preset_range("all"))
            self.stdout.write(
                f"dry-run: {len(report.shopify_matches)} Shopify match(es), "
                f"{len(report.unmatched_payouts)} unmatched payout(s), "
                f"{len(report.supplier_matches)} supplier match(es), "
                f"{len(report.unmatched_charges)} unmatched Inkthreadable charge(s)"
            )
            return

        result = apply_suggestions()
        self.stdout.write(
            f"created={result['created']} updated={result['updated']} "
            f"skipped={result['skipped']} deleted={result['deleted']}"
        )
