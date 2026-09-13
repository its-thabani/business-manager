"""Shared bookkeeping for a synchronisation run."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from django.utils import timezone

from apps.integrations.models import SyncRun, SyncStatus


class SyncStats:
    """Mutable counters updated as records are written."""

    def __init__(self):
        self.seen = 0
        self.created = 0
        self.updated = 0
        self.failed = 0
        self.cursor = ""
        self.extra: dict = {}

    def mark(self, created: bool) -> None:
        if created:
            self.created += 1
        else:
            self.updated += 1

    def as_fields(self) -> dict:
        return {
            "records_seen": self.seen,
            "records_created": self.created,
            "records_updated": self.updated,
            "records_failed": self.failed,
            "cursor": self.cursor,
            "stats": self.extra,
        }


@contextmanager
def sync_run(service: str, resource: str):
    """Create a SyncRun, yield stats, and close the row on the way out."""
    run = SyncRun.objects.create(
        service=service,
        resource=resource,
        status=SyncStatus.RUNNING,
        started_at=timezone.now(),
    )
    stats = SyncStats()
    try:
        yield run, stats
    except Exception as exc:
        run.status = SyncStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = timezone.now()
        for key, value in stats.as_fields().items():
            setattr(run, key, value)
        run.save()
        raise
    else:
        if stats.failed and (stats.created or stats.updated):
            run.status = SyncStatus.PARTIAL
        elif stats.failed:
            run.status = SyncStatus.FAILED
        else:
            run.status = SyncStatus.SUCCESS
        run.finished_at = timezone.now()
        for key, value in stats.as_fields().items():
            setattr(run, key, value)
        run.save()


def isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()
