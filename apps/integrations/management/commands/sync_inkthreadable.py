"""Pull fulfilment orders from Inkthreadable into the local database."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.integrations.base import ConfigurationError, IntegrationError
from apps.integrations.inkthreadable.sync import sync_orders


class Command(BaseCommand):
    help = "Synchronise Inkthreadable orders. Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="Ignore the incremental cursor and pull everything again.",
        )

    def handle(self, *args, **options):
        try:
            run = sync_orders(full=options["full"])
        except ConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except IntegrationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"orders: {run.status}  "
            f"seen={run.records_seen} created={run.records_created} "
            f"updated={run.records_updated} failed={run.records_failed}"
        )
        if run.error:
            self.stdout.write(self.style.ERROR(run.error))
