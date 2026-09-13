"""One-off: two Monzo credits that were Salary are Direct Sales Income.

Run against Neon:

    DATABASE_URL='postgresql://...' .venv/bin/python scripts/reclassify_personal_tshirt_sales.py
"""

from __future__ import annotations

import os
import socket
import sys
import time
from datetime import date
from pathlib import Path

# macOS often gets Neon's IPv6 address and then "No route to host".
# Prefer IPv4 before Django opens a connection.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import connection  # noqa: E402
from django.db.utils import OperationalError  # noqa: E402

from apps.analytics.cashflow import compute_cash_metrics  # noqa: E402
from apps.core.periods import DateRange  # noqa: E402
from apps.finance.models import BankTransaction, Category  # noqa: E402

HOST = connection.settings_dict.get("HOST") or connection.settings_dict.get("NAME")
print("HOST:", HOST)
if "neon" not in str(HOST).lower():
    raise SystemExit("Not Neon. Set DATABASE_URL on the same command line.")

INCOME = None
for attempt in range(1, 6):
    try:
        INCOME = Category.objects.get(name="Direct Sales Income")
        break
    except OperationalError as exc:
        print(f"connect attempt {attempt} failed: {exc}")
        time.sleep(3)
if INCOME is None:
    raise SystemExit("Could not reach Neon after 5 tries. Wait 20 seconds and run again.")

IDS = (
    "mm_0000B9MOKxb8m12dcx9cfJ",
    "mm_0000B9QTwe7utOfZ9ql1bW",
)
NOTE = (
    "T-shirt paid to a personal account and transferred into Monzo. "
    "Not owner drawings — Direct Sales Income. Locked so rules cannot reclassify."
)

rows = list(BankTransaction.objects.filter(external_id__in=IDS).select_related("category"))
if len(rows) != 2:
    raise SystemExit(f"Expected 2 rows, found {len(rows)}")

for txn in rows:
    print("before", txn.external_id, txn.amount, txn.category)
    txn.set_manual_category(INCOME, lock=True)
    txn.internal_notes = NOTE
    txn.save(update_fields=["internal_notes", "updated_at"])
    print("after ", txn.external_id, txn.amount, txn.category)

metrics = compute_cash_metrics(DateRange(date(2026, 1, 1), date(2026, 9, 13)))
print("YTD profit exc salary/tithe", metrics.net_profit_excluding_distributions)
print("YTD revenue", metrics.revenue)
