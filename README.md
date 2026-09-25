# StayLit Apparel — Business Manager

An internal business management application for the StayLit Apparel print-on-demand
business. It replaces the manual Shopify → Inkthreadable → Monzo → spreadsheet
workflow with a single system that can answer: **is the business making money,
where is it coming from, where is it going, and which products are worth selling?**

Built with Django and Python. Runs on SQLite locally and Postgres in production.

---

## Current status

Stages 1 and 2 are in place. Later stages are listed in [Roadmap](#roadmap).

| Area | State |
| --- | --- |
| Finance data model | Done — 2,583 historical transactions imported |
| Monzo CSV import | Done — idempotent, deduplicating |
| Historical spreadsheet import | Done — non-destructive, read-only on the workbook |
| Transaction categorisation | Done — configurable rules with manual overrides |
| Financial calculation engine | Done — reproduces the spreadsheet exactly, and corrects it |
| Validation against the spreadsheet | Done — every figure reconciled |
| Shopify integration | Client + sync. Uses client-credentials grant, or a static `shpat_` token |
| Inkthreadable integration | Client + sync. SHA1-signed requests against the official API |
| Product / variant mapping UI | Done — `/mapping/` plus Inkthreadable blanks |
| Product, order, group, customer, shipping, discount and refund analytics | Done |
| Reconciliation | Done — Shopify days → Stripe/Adyen, invoices → Inkthreadable |
| Simulations | Done — `/simulate/` (does not write the books) |
| New-product price | Done — `/price/` (does not write the books) |
| Insights, AI, Render | Insights and operator guide done. CSV exports instead of an in-app AI. Render next. |

---

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

cp .env.example .env          # then fill in the values

.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_finance
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Then open <http://127.0.0.1:8000/>. The **i** button next to Admin opens the
operator guide. Technical notes stay in this README.

### Load the historical data

```bash
# Both transaction sheets. The workbook is only ever read, never written to.
.venv/bin/python manage.py import_finance_dashboard "files/Finance Dashboard.xlsx"

# Prove the import reproduces the spreadsheet's own figures.
.venv/bin/python manage.py validate_against_spreadsheet "files/Finance Dashboard.xlsx"
```

### Import a new Monzo export

```bash
.venv/bin/python manage.py import_monzo "path/to/Monzo Data Export.csv"
```

Safe to run on overlapping exports: rows already held are recognised and left
untouched, including any category set by hand. Add `--dry-run` to preview.

### Check the integrations and sync

```bash
.venv/bin/python manage.py test_connections --sample
.venv/bin/python manage.py sync_shopify
.venv/bin/python manage.py sync_inkthreadable
```

Then open <http://127.0.0.1:8000/integrations/>, <http://127.0.0.1:8000/products/>,
<http://127.0.0.1:8000/orders/> and <http://127.0.0.1:8000/reconciliation/>.

```bash
.venv/bin/python manage.py suggest_reconciliation
```

### Run the tests

```bash
.venv/bin/python -m pytest
```

---

## How the money is modelled

### Sign convention

Every amount is stored **signed, from the bank's point of view**: positive is
money in, negative is money out. An imported row never has its sign reinterpreted.

All monetary values are `Decimal` quantised to two decimal places. Floats are
never used for money, because binary floating point cannot represent decimal
currency exactly and the error compounds once thousands of rows are summed.

### Classification is data, not code

What a transaction *means* financially comes from its category's **kind**, not
from its name and not from the sign of the amount:

| Kind | Meaning |
| --- | --- |
| `REVENUE` | Sales income. A negative amount here is a refund and reduces revenue. |
| `COGS` | Supplier costs. A positive amount here is a supplier credit and reduces cost. |
| `SHIPPING` | Postage and despatch |
| `FEES` | Payment and platform fees |
| `OPERATING` | Overheads: software, hosting, marketing |
| `DISTRIBUTION` | Owner drawings and tithe — the "exc. salary & tithe" view excludes these |
| `TRANSFER` | Moving money between own accounts. Not income or expenditure. |
| `EXCLUDED` | Deliberately outside all reporting |

Because reporting keys off `kind`, categories can be renamed or split without
breaking a single calculation.

### Profit is layered

```
  Revenue  (net of customer refunds)
- Cost of goods sold
- Shipping
- Payment fees
  ─────────────────────────────────
= Contribution profit
- Operating expenses
  ─────────────────────────────────
= Operating profit
- Owner distributions (salary, tithe)
  ─────────────────────────────────
= Net profit
```

### Revenue, profit and cash are three different things

The figures produced from bank transactions describe **cash movement**. A Shopify
order raises revenue on the day it is placed; the money arrives days later, net of
card fees, and may be partly refunded afterwards. Order-level revenue and profit
are computed separately from Shopify and supplier data. Reconciling the two is the
job of the Reconciliation section, not something to paper over by treating one as
the other.

### Unknown is not zero

Where a figure cannot be calculated reliably it is `None` and displays as `—`.
A margin on zero revenue is unknown, not 0%.

---

## Two calculation modes

`apps/analytics/cashflow.py` can compute in either mode:

- **`STANDARD`** — the corrected treatment described above.
- **`LEGACY`** — reproduces the spreadsheet exactly: every credit is revenue and
  every debit is an expense, regardless of category.

`LEGACY` exists so the import can be proved correct against figures that are
already trusted, before anything changes. `validate_against_spreadsheet` uses it,
and quantifies the difference between the two rather than asserting that the new
one is better.

---

## What validation found in the spreadsheet

The 2026 dashboard totals and all twelve monthly figures reproduce **to the penny**.
Three defects in the workbook were found and are each accounted for automatically:

1. **Formula ranges start at the wrong row.** Every dashboard formula reads
   `'Historic Data'!C10:C1008`, silently omitting rows 2–9. Those are real
   transactions from 12–15 May 2025 (the sheet is sorted newest-first), so
   £165.00 of income and £71.34 of costs are missing from the "Last Year" figures.

2. **A broken reference.** Cell `K11` ("Last Year expenses exc. salary & tithe")
   contains `#REF!`. The cell still shows a plausible number, £4,126.94, but the
   correct figure is £8,261.63 — a £4,134.69 error that also flows into `G18`.

3. **A duplicated row.** Row 106 of `Transactions` repeats row 104: the same Monzo
   transaction ID, `mm_0000Ax0IXlHWRnthOvJ6B9`, for £62.91. A bank ID cannot
   describe two payments, so August 2025 costs are overstated by £62.91. It is
   imported once and recorded as an import issue.

Separately, the sign-based logic misclassifies money in the current year:

- £54.00 of personal transfers labelled `Salary` counted as **business revenue**.
- A £21.73 Inkthreadable refund counted as **revenue** instead of reducing costs.
- A £29.99 customer refund counted as an **expense** instead of reducing revenue.

Net effect on the current year: revenue overstated by £75.73, and profit
excluding salary and tithe overstated by £54.00 (£109.68 reported, £55.68 actual).

The original workbook is untouched.

---

## Categorisation

Rules live in the database and are edited in the admin. Each has a pattern, a
match type (contains, exact, starts with, regex, …), which text to look at, an
optional sign restriction, and a priority. They are evaluated lowest priority
first and **the first match wins**.

Matching ignores case and collapses runs of whitespace, so a pattern does not have
to reproduce the exact padding a bank uses — `INKTHREADABLE` matches both
`INKTHREADABLE          BLACKBURN     GBR` and `INKTHREADABLE\tON 12 MAY BCC`.

The sign restriction matters more than it looks. Both a Shopify payout and the
Shopify subscription contain "SHOPIFY"; only the direction of the money separates
revenue from a hosting cost.

**Manual decisions are never overwritten.** Setting a category by hand on
Expenses locks it. Spreadsheet labels are starting points: **Apply rules**
will replace them when a more specific rule matches (for example HMRC → Tax).
Monzo CSV import only fills uncategorised gaps, so it does not silently rewrite
existing books.

All 31 rules from the `Categorisation` sheet are seeded, plus additions
(prefixed `ADDED` in their names) for Stripe settlements, Facebook ads, and
HMRC tax payments.

Running the rules against the rows already categorised in the spreadsheet gives
**89.5% agreement on financial treatment**. Of the remainder, 263 rows match no
rule at all — mostly one-off customer transfers — which is precisely the manual
work still to be automated.

---

## Deduplication

Re-importing overlapping exports must never double-count. Each transaction gets a
`fingerprint`, enforced unique per account by a database constraint:

- **With a bank transaction ID**, the ID alone is the fingerprint. Monzo tidies
  merchant names between exports, so the text cannot be trusted, but the ID can.
  A repeated ID means a row was entered twice and is rejected as a data issue.
- **Without one** (the legacy `Historic Data` sheet), the fingerprint is the date,
  amount and narrative, plus an **occurrence index**. The index matters: two
  Inkthreadable orders of the same value on the same day are ordinary for
  print-on-demand and are two real payments. Without it, 33 genuine transactions
  were being silently collapsed.

Because the same file always produces the same indices, re-importing stays
idempotent.

---

## Project layout

```
config/                     Django settings, URLs, WSGI
apps/
  core/                     Money, date ranges, abstract models
  finance/                  Transactions, categories, rules, importers
    importers/              Monzo CSV and Finance Dashboard workbook
    management/commands/    seed_finance, import_monzo,
                            import_finance_dashboard, validate_against_spreadsheet
  analytics/                Deterministic financial calculations
  integrations/             External API clients
    shopify/                GraphQL Admin API client
    inkthreadable/          Supplier API client
  catalog/  sales/          Products, variants, mappings; orders, customers
  supplier/  web/           Supplier records; user interface
tests/                      pytest suite
files/                      Source data (gitignored — contains real records)
```

Business logic never lives in a view or a template. External API shapes never
leak past their adapter.

---

## Configuration

All configuration comes from the environment, loaded from a gitignored `.env` in
development. **No credential is ever hard-coded.** See `.env.example`.

### Shopify

Either:

- `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` — exchanged for a 24-hour Admin
  API token via the client-credentials grant (Dev Dashboard apps in the same
  organisation as the store), or
- `SHOPIFY_ACCESS_TOKEN` — a static Admin API token starting `shpat_`, from
  **Settings → Apps and sales channels → Develop apps → API credentials**.

Required scopes: `read_orders`, `read_all_orders`, `read_products`,
`read_customers`. `read_all_orders` matters — without it the API returns only the
last 60 days of orders, which would quietly truncate all historical analysis.

The client uses the **GraphQL** Admin API. Shopify has restricted the REST order
endpoints for new apps, so GraphQL avoids a forced rewrite. It also fetches an
order with its line items, refunds, shipping and fees in one request, and reads
the cost budget from each response to pause before being throttled.

### Inkthreadable

Official API: `https://www.inkthreadable.co.uk/api`.

Every request is signed as `SHA1(payload + secret key)` (40-character hex). The
secret itself is never placed in the URL — only `AppId` and the hash. GET
requests sign the query string after `?`, excluding `Signature`.

There is no catalogue listing endpoint. Supplier products and unit costs are
taken from order line items (`pn`, `price`, size/colour options).

---

## Hosted production (Render + Neon)

This is the live setup as of September 2026. Do not point a new deploy at an
empty database and resync Shopify / Inkthreadable unless you intend to replace
the books.

| Piece | Where |
| --- | --- |
| App | [https://staylit-business-manager.onrender.com](https://staylit-business-manager.onrender.com) |
| Code | [https://github.com/its-thabani/business-manager](https://github.com/its-thabani/business-manager) |
| Web host | Render — service `staylit-business-manager`, **Free** plan, Frankfurt |
| Database | Neon Postgres — project `staylit-business-manager`, AWS `eu-west-2` (London). Not Render Postgres. |
| Operator login | Username `laura`. First password from `OPERATOR_PASSWORD` (default `1234` only if that user was created empty). Existing passwords are never reset on deploy. |

The live books were copied from the original laptop SQLite file into Neon
(`dumpdata` / `loaddata`). They are the source of truth. Local `db.sqlite3` is
for development only and will drift once Laura uses the hosted site.

Free Render sleeps after about 15 minutes idle. Free Neon also scales to zero.
The first request after a break can take a minute. That is the host, not a
failed deploy.

`order_number` is a `BigIntegerField` because Shopify ids do not fit in a
32-bit Postgres `integer`. SQLite hid that; do not revert the column.

### Environment variables on Render

Set these on the web service (never commit them). `DATABASE_URL` must be the
Neon **direct** connection string (no `-pooler` in the hostname).

| Key | Notes |
| --- | --- |
| `DATABASE_URL` | Neon URI with `sslmode=require` |
| `DJANGO_SECRET_KEY` | Generated on Render |
| `DJANGO_DEBUG` | `False` |
| `PYTHON_VERSION` | `3.13.0` |
| `SHOPIFY_STORE` | `scctj4-i8.myshopify.com` |
| `SHOPIFY_API_VERSION` | `2025-01` |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` / `SHOPIFY_ACCESS_TOKEN` | From the local `.env` |
| `INKTHREADABLE_APP_ID` | `APP-00146434` |
| `INKTHREADABLE_SECRET_KEY` | From the local `.env` |
| `INKTHREADABLE_BASE_URL` | `https://www.inkthreadable.co.uk/api` |
| `INKTHREADABLE_AUTH_STYLE` | `query` |
| `OPERATOR_PASSWORD` | Only used if `laura` does not already exist |

`build.sh` runs `migrate`, `seed_finance` and `bootstrap_operator`. Seed is
idempotent. Bootstrap does not overwrite Laura’s password.

### Deploying a code change

The GitHub App listing did not see this private repo, so the service was
connected as a **public Git repository**. That path does **not** auto-deploy
on `git push`.

1. Commit and push to `main` on GitHub.
2. In Render → `staylit-business-manager` → **Manual Deploy** → **Deploy latest commit**.

If the GitHub App later lists the repo, you can reconnect it and turn
auto-deploys on. Do not create a second web service.

Do not add a Render Postgres. Keep using the existing Neon `DATABASE_URL`.

With `DJANGO_DEBUG=False` the app enforces HTTPS, HSTS and secure cookies.

---

## Roadmap

Each stage is built, tested and verified before the next begins.

1. ~~Architecture, data model, historical import, categorisation, validation~~ ✅
2. ~~Shopify + Inkthreadable clients and sync~~ ✅
3. ~~Product and variant mapping UI~~ ✅
5. ~~Order profitability~~ ✅
6. ~~Product analytics~~ ✅
7. ~~Dashboard UI with date ranges, comparisons and charts~~ ✅
8. ~~Reconciliation — Shopify orders → payouts → Monzo transactions~~ ✅
9. ~~What-if simulations — price, supplier cost, shipping, discount~~ ✅
10. ~~Business insights, computed from real data with the numbers shown~~ ✅
11. ~~Customer analytics — new / returning, repeat rate, AOV~~ ✅
12. ~~Shipping analytics — charged vs cost, free-postage impact~~ ✅
13. ~~Discount analytics — codes, revenue given away, profit when costs are known~~ ✅
14. ~~Refund analytics — rate by product / variant~~ ✅
15. ~~Product groups — hoodies, tees, sweatshirts from titles~~ ✅
16. ~~Data health page~~ ✅
17. ~~CSV downloads of tables (instead of an in-app AI)~~ ✅
18. ~~Render deployment~~ ✅ — live on Render + Neon (see above)

Parked follow-ups (unlock-profit queue, tax-year pack, and what not to build)
are in [docs/FUTURE.md](docs/FUTURE.md).

---

## Principles

- Historical data is never overwritten or destroyed.
- Financial calculations live in the backend and are deterministic and reproducible.
- Every figure can be traced to the rows it came from.
- Imports are idempotent; a partial import is always explainable.
- Where something cannot be calculated reliably, say so instead of estimating.
- An AI assistant explains numbers; it never produces them.
