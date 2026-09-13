"""Abstract base models shared across the application."""

from __future__ import annotations

from django.db import models


class TimeStampedModel(models.Model):
    """Records when a row was first stored and last changed.

    Every table carries these so data staleness can be reported on the Data
    Health page and so imports can be audited after the fact.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MoneyField(models.DecimalField):
    """A signed currency amount stored as an exact decimal.

    12 digits with 2 decimal places allows values up to 9,999,999,999.99, far
    beyond anything this business will record, while keeping arithmetic exact.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(*args, **kwargs)


class QuantityField(models.DecimalField):
    """A quantity that may be fractional (e.g. a partially refunded unit)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 3)
        super().__init__(*args, **kwargs)
