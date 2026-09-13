"""Import historical transactions from the existing Finance Dashboard workbook."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.finance.importers import import_finance_dashboard


class Command(BaseCommand):
    help = (
        "Import the Transactions and Historic Data sheets from the Finance Dashboard "
        "workbook. The workbook is only ever read, never written to. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to Finance Dashboard.xlsx")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be imported without writing anything",
        )

    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser()
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        reports = import_finance_dashboard(path, dry_run=options["dry_run"])
        if not reports:
            raise CommandError(
                "No recognised sheets found. Expected a 'Transactions' and/or 'Historic Data' sheet."
            )

        for report in reports:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(report.filename))
            self.stdout.write(report.describe())

        total_created = sum(r.created for r in reports)
        total_duplicate = sum(r.duplicates for r in reports)

        self.stdout.write("")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Import complete: {total_created} created, {total_duplicate} already present."
                )
            )
        self.stdout.write(
            "Spreadsheet categories were imported as-is and locked, so re-running the "
            "categorisation rules will not overwrite them."
        )
