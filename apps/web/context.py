"""Template context shared by every page."""

from __future__ import annotations

from apps.core.periods import PRESETS, parse_iso_date


def range_query(request):
    """Preserve the current date window on in-page links."""
    start = parse_iso_date(request.GET.get("start"))
    end = parse_iso_date(request.GET.get("end"))
    if start and end:
        query = f"start={start.isoformat()}&end={end.isoformat()}"
    else:
        preset = request.GET.get("range", "ytd")
        if preset not in PRESETS:
            preset = "ytd"
        query = f"range={preset}"
    return {"range_query": query}
