"""Seed the category taxonomy and categorisation rules."""

from django.core.management.base import BaseCommand

from apps.finance.seed import seed_all


class Command(BaseCommand):
    help = "Create or update the finance categories and categorisation rules. Safe to re-run."

    def handle(self, *args, **options):
        result = seed_all()
        self.stdout.write(
            f"Categories: {result.categories_created} created, {result.categories_updated} updated"
        )
        self.stdout.write(f"Rules:      {result.rules_created} created, {result.rules_updated} updated")
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"  ! {warning}"))
        self.stdout.write(self.style.SUCCESS("Seed complete."))
