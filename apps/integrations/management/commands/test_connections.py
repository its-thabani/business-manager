"""Verify that the external integrations are reachable and correctly configured.

Run this before any sync. It reports on each integration independently, so a
problem with one does not hide the state of the other.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.integrations.inkthreadable.client import InkthreadableClient
from apps.integrations.shopify.client import ShopifyClient


class Command(BaseCommand):
    help = "Check connectivity and credentials for Shopify and Inkthreadable."

    def add_arguments(self, parser):
        parser.add_argument(
            "--service",
            choices=["shopify", "inkthreadable", "all"],
            default="all",
            help="Which integration to test (default: all)",
        )
        parser.add_argument(
            "--sample",
            action="store_true",
            help="On success, also fetch a small sample of real records to prove reads work",
        )

    def handle(self, *args, **options):
        service = options["service"]
        results = []

        if service in {"shopify", "all"}:
            results.append(self._check_shopify(sample=options["sample"]))
        if service in {"inkthreadable", "all"}:
            results.append(self._check_inkthreadable(sample=options["sample"]))

        self.stdout.write("")
        failed = [r for r in results if not r]
        if failed:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(failed)} of {len(results)} integration(s) are not working yet. "
                    "The rest of the application does not depend on them, so historical "
                    "finance data remains usable meanwhile."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("All integrations are reachable."))

    def _check_shopify(self, *, sample: bool) -> bool:
        self.stdout.write(self.style.MIGRATE_HEADING("\nShopify"))
        client = ShopifyClient()
        check = client.check_connection()
        self._render(check)
        if not check.ok or not sample:
            return check.ok

        try:
            products = []
            for product in client.iter_products(page_size=5):
                products.append(product)
                if len(products) >= 5:
                    break
            self.stdout.write(f"      Products readable: {len(products)} fetched")
            for product in products[:5]:
                variants = product.get("variants", {}).get("edges", [])
                self.stdout.write(
                    f"        · {product['title']}  ({product.get('productType') or 'no type'}, "
                    f"{len(variants)} variant(s))"
                )

            orders = []
            for order in client.iter_orders(page_size=3):
                orders.append(order)
                if len(orders) >= 3:
                    break
            total = client.count_orders()
            self.stdout.write(
                f"      Orders readable: {len(orders)} fetched"
                + (f" (store reports {total} in total)" if total is not None else "")
            )
            for order in orders:
                money = order["totalPriceSet"]["shopMoney"]["amount"]
                items = len(order.get("lineItems", {}).get("edges", []))
                self.stdout.write(
                    f"        · {order['name']}  {order['createdAt'][:10]}  "
                    f"{money} {order['currencyCode']}  {items} line(s)  "
                    f"{order['displayFinancialStatus']}"
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            self.stdout.write(self.style.ERROR(f"      Sample read failed: {type(exc).__name__}: {exc}"))
            return False
        return True

    def _check_inkthreadable(self, *, sample: bool) -> bool:
        self.stdout.write(self.style.MIGRATE_HEADING("\nInkthreadable"))
        client = InkthreadableClient()
        check = client.check_connection()
        self._render(check)
        if not check.ok or not sample:
            return check.ok

        try:
            orders = client.list_orders(page=1, limit=3)
            self.stdout.write(f"      Orders readable: {len(orders)} fetched on page 1")
            for order in orders[:3]:
                summary = order.get("summary") or {}
                items = order.get("items") or []
                self.stdout.write(
                    f"        · #{order.get('id')}  {order.get('created_at') or '?'}  "
                    f"{order.get('status')}  "
                    f"{summary.get('total', '?')} {summary.get('currency', '')}  "
                    f"{len(items)} item(s)  ext={order.get('external_id') or '—'}"
                )
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"      Sample read failed: {type(exc).__name__}: {exc}"))
            return False
        return True

    def _render(self, check) -> None:
        style = self.style.SUCCESS if check.ok else self.style.ERROR
        self.stdout.write(style(f"  {'PASS' if check.ok else 'FAIL'}  {check.service}"))
        if check.detail:
            for line in str(check.detail).splitlines():
                self.stdout.write(f"      {line}")
        for key, value in check.samples.items():
            self.stdout.write(f"      {key}: {value}")
        if check.hint:
            self.stdout.write(self.style.WARNING(f"      → {check.hint}"))
