"""Match stored Inkthreadable orders to Shopify without calling the API."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.integrations.inkthreadable.sync import relink_supplier_orders


class Command(BaseCommand):
    help = (
        "Link Inkthreadable fulfilments to Shopify orders using the saved "
        "payload. Does not invent matches. Existing links are left alone."
    )

    def handle(self, *args, **options):
        result = relink_supplier_orders()
        self.stdout.write(
            f"linked={result['linked']} already={result['already']} "
            f"unmatched={result['unmatched']}"
        )
