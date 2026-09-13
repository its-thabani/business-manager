"""Import a Monzo CSV export."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.finance.categorisation import categorise
from apps.finance.importers import import_monzo_csv
from apps.finance.models import BankAccount, BankTransaction


class Command(BaseCommand):
    help = (
        "Import transactions from a Monzo CSV export. Safe to run on overlapping "
        "exports: rows already held are skipped and never modified."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the Monzo CSV export")
        parser.add_argument(
            "--account",
            default="Monzo Business",
            help="Name of the bank account to import into (default: Monzo Business)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be imported without writing anything",
        )
        parser.add_argument(
            "--no-categorise",
            action="store_true",
            help="Skip running the categorisation rules over the new rows",
        )

    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser()
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        account, created = BankAccount.objects.get_or_create(
            name=options["account"],
            defaults={"institution": "Monzo", "currency": "GBP", "is_primary": True},
        )
        if created:
            self.stdout.write(f"Created bank account {account.name!r}")

        report = import_monzo_csv(path, account=account, dry_run=options["dry_run"])
        self.stdout.write(report.describe())

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run — nothing was written."))
            return

        if not options["no_categorise"] and report.created:
            result = categorise(
                BankTransaction.objects.filter(import_batch_id=report.batch_id),
                only_uncategorised=True,
            )
            self.stdout.write(f"\nCategorisation: {result.summary}")
            if result.uncategorised:
                self.stdout.write(
                    self.style.WARNING(
                        f"{result.uncategorised} new transaction(s) matched no rule and need a category. "
                        "Review them in the admin, or add a rule."
                    )
                )

        self.stdout.write(self.style.SUCCESS("\nImport complete."))
