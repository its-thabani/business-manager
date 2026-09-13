"""Pull products and orders from Shopify into the local database."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.integrations.base import ConfigurationError, IntegrationError
from apps.integrations.shopify.sync import sync_all, sync_orders, sync_products


class Command(BaseCommand):
    help = "Synchronise Shopify products and/or orders. Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--resource",
            choices=["products", "orders", "all"],
            default="all",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Ignore the incremental cursor and pull everything again.",
        )

    def handle(self, *args, **options):
        resource = options["resource"]
        full = options["full"]
        try:
            if resource == "products":
                runs = [sync_products(full=full)]
            elif resource == "orders":
                runs = [sync_orders(full=full)]
            else:
                runs = sync_all(full=full)
        except ConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except IntegrationError as exc:
            raise CommandError(str(exc)) from exc

        for run in runs:
            self.stdout.write(
                f"{run.resource}: {run.status}  "
                f"seen={run.records_seen} created={run.records_created} "
                f"updated={run.records_updated} failed={run.records_failed}"
            )
            if run.error:
                self.stdout.write(self.style.ERROR(f"  {run.error}"))
