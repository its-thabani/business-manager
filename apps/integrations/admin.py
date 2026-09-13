from django.contrib import admin

from apps.integrations.models import SyncRun


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "service",
        "resource",
        "status",
        "records_seen",
        "records_created",
        "records_updated",
        "records_failed",
        "cursor",
    )
    list_filter = ("service", "resource", "status")
    readonly_fields = tuple(f.name for f in SyncRun._meta.fields)
    date_hierarchy = "started_at"

    def has_add_permission(self, request) -> bool:
        return False
