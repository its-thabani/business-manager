"""Start long Shopify / Inkthreadable pulls from the UI without blocking a tab.

The work runs in a background thread so the page can return immediately.
Progress is the existing SyncRun rows. A stale 'running' row older than six
hours is marked failed so a crashed job cannot block the next click.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone

from apps.integrations.models import SyncRun, SyncStatus

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_running: set[str] = set()
_STALE_AFTER = timedelta(hours=6)


def mark_stale_runs() -> int:
    cutoff = timezone.now() - _STALE_AFTER
    return SyncRun.objects.filter(
        status=SyncStatus.RUNNING,
        started_at__lt=cutoff,
    ).update(
        status=SyncStatus.FAILED,
        finished_at=timezone.now(),
        error="Stopped because the previous run sat unfinished for more than six hours.",
    )


def is_sync_running() -> bool:
    mark_stale_runs()
    if _running:
        return True
    return SyncRun.objects.filter(status=SyncStatus.RUNNING).exists()


def start_job(key: str, fn) -> bool:
    """Start ``fn`` in a daemon thread. Returns False if that job is already up."""
    mark_stale_runs()
    with _lock:
        if key in _running or SyncRun.objects.filter(status=SyncStatus.RUNNING).exists():
            return False
        _running.add(key)

    def run() -> None:
        close_old_connections()
        try:
            fn()
        except Exception:
            logger.exception("background job %s failed", key)
        finally:
            close_old_connections()
            with _lock:
                _running.discard(key)

    threading.Thread(target=run, name=f"sync-{key}", daemon=True).start()
    return True
