"""Small helpers for turning third-party payloads into our types.

Kept here so the Shopify and Inkthreadable adapters do not each reinvent
datetime and money parsing, and so a bad value from either API becomes
``None`` rather than crashing a whole sync.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from dateutil.parser import isoparse
from django.utils import timezone

from apps.core.money import to_decimal


def parse_datetime(value: object) -> datetime | None:
    """Parse an ISO timestamp into an aware datetime, or None if absent."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = isoparse(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def local_date(value: object):
    """Calendar day in the business timezone, or None."""
    dt = value if isinstance(value, datetime) else parse_datetime(value)
    if dt is None:
        return None
    return timezone.localtime(dt).date()


def money(value: object) -> Decimal | None:
    """A currency amount from any of the shapes the two APIs use."""
    if isinstance(value, dict):
        if "shopMoney" in value:
            value = (value.get("shopMoney") or {}).get("amount")
        elif "amount" in value:
            inner = value["amount"]
            value = inner.get("amount") if isinstance(inner, dict) else inner
    return to_decimal(value, default=None)


def gid_suffix(gid: str | None) -> str:
    """Last segment of a Shopify GID, e.g. ``gid://shopify/Order/12`` → ``12``."""
    if not gid:
        return ""
    return str(gid).rstrip("/").rsplit("/", 1)[-1]


def as_list(payload: object) -> list:
    """Normalise a payload that might be a list, a wrapper, or a single record."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "orders", "products", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def unwrap_order(record: object) -> dict:
    """Inkthreadable wraps each record as ``{"order": {...}}``; tolerate both."""
    if isinstance(record, dict) and isinstance(record.get("order"), dict) and "id" not in record:
        return record["order"]
    return record if isinstance(record, dict) else {}
