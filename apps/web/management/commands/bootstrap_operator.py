"""Create the Laura staff login if it does not exist."""

from django.core.management.base import BaseCommand

from apps.web.bootstrap import OPERATOR_USERNAME, ensure_operator_user


class Command(BaseCommand):
    help = "Create the operator login (laura) if missing. Does not reset a password."

    def handle(self, *args, **options):
        user, created = ensure_operator_user()
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created staff user {OPERATOR_USERNAME}"))
        else:
            self.stdout.write(f"Staff user {user.username} already exists")
