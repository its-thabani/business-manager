"""One-time operator account. Safe to run on every deploy."""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model

OPERATOR_USERNAME = "laura"


def ensure_operator_user() -> tuple[object, bool]:
    """Create the Laura login if it is missing. Never reset an existing password."""
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=OPERATOR_USERNAME,
        defaults={
            "is_staff": True,
            "is_superuser": True,
            "first_name": "Laura",
        },
    )
    if created:
        user.set_password(os.environ.get("OPERATOR_PASSWORD", "1234"))
        user.save()
        return user, True
    changed = False
    if not user.is_staff or not user.is_superuser:
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
        changed = True
    return user, changed
