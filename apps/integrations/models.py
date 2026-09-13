"""Records of synchronisation runs.

The Data Health page reads these to say when each source was last updated and
whether the last attempt succeeded. The ``cursor`` on the latest successful run
is what makes the next sync incremental.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel


class SyncService(models.TextChoices):
    SHOPIFY = "shopify", "Shopify"
    INKTHREADABLE = "inkthreadable", "Inkthreadable"


class SyncResource(models.TextChoices):
    PRODUCTS = "products", "Products"
    ORDERS = "orders", "Orders"
    ALL = "all", "All"


class SyncStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Succeeded"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"


class SyncRun(TimeStampedModel):
    """One attempt to pull a resource from an external system."""

    service = models.CharField(max_length=30, choices=SyncService.choices, db_index=True)
    resource = models.CharField(max_length=30, choices=SyncResource.choices, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.RUNNING,
        db_index=True,
    )
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    cursor = models.CharField(
        max_length=80,
        blank=True,
        help_text="Incremental watermark written on success (updated_at or since_id)",
    )
    records_seen = models.IntegerField(default=0)
    records_created = models.IntegerField(default=0)
    records_updated = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)
    error = models.TextField(blank=True)
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["service", "resource", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.service} {self.resource} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"

    @classmethod
    def latest_success(cls, service: str, resource: str) -> SyncRun | None:
        return cls.objects.filter(
            service=service,
            resource=resource,
            status=SyncStatus.SUCCESS,
        ).first()
