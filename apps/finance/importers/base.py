"""Shared import machinery.

Every importer reports what it did in the same shape, so the UI and the
management commands can present results uniformly, and so a partial import is
always explainable rather than silently losing rows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass
class RowIssue:
    """A row that could not be imported cleanly."""

    row_number: int
    reason: str
    raw: dict

    def __str__(self) -> str:
        return f"row {self.row_number}: {self.reason}"


@dataclass
class ImportReport:
    """The outcome of one import run."""

    source: str
    filename: str = ""
    rows_read: int = 0
    created: int = 0
    duplicates: int = 0
    skipped: int = 0
    issues: list[RowIssue] = field(default_factory=list)
    batch_id: int | None = None
    # Kept so the caller can sanity-check the totals against the source file.
    total_in: Decimal = Decimal("0.00")
    total_out: Decimal = Decimal("0.00")
    earliest: object | None = None
    latest: object | None = None

    def note_amount(self, amount: Decimal) -> None:
        if amount >= 0:
            self.total_in += amount
        else:
            self.total_out += amount

    def note_date(self, when) -> None:
        if when is None:
            return
        if self.earliest is None or when < self.earliest:
            self.earliest = when
        if self.latest is None or when > self.latest:
            self.latest = when

    @property
    def net(self) -> Decimal:
        return self.total_in + self.total_out

    def describe(self) -> str:
        lines = [
            f"Source:      {self.source}",
            f"File:        {self.filename or '(none)'}",
            f"Rows read:   {self.rows_read}",
            f"Created:     {self.created}",
            f"Duplicates:  {self.duplicates}  (already present, left untouched)",
            f"Skipped:     {self.skipped}",
        ]
        if self.earliest and self.latest:
            lines.append(f"Date range:  {self.earliest} to {self.latest}")
        lines.append(f"Money in:    {self.total_in:,.2f}")
        lines.append(f"Money out:   {self.total_out:,.2f}")
        lines.append(f"Net:         {self.net:,.2f}")
        if self.issues:
            lines.append(f"Issues:      {len(self.issues)}")
            for issue in self.issues[:20]:
                lines.append(f"  - {issue}")
            if len(self.issues) > 20:
                lines.append(f"  ... and {len(self.issues) - 20} more")
        return "\n".join(lines)


def file_checksum(path: str | Path) -> str:
    """SHA-256 of a file's contents, used to spot a re-upload of the same export."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
