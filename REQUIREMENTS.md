# StayLit Apparel — requirements checklist

Living checklist against the original brief and later constraints.
Tick items only when they are in the app and tested, not when they are planned.

Status: `done` · `partial` · `todo`

The two halves of the product:

1. **Finance** — what the spreadsheet did: bank cash, categorisation, P&L, import, reconciliation.
2. **Insights** — Shopify / Inkthreadable: products, orders, groups, mapping, simulations, customers, shipping, discounts, refunds, later AI.

Bank figures must remain usable even when Shopify or Inkthreadable data is thin.

---

## Original build order

| # | Stage | Status | Where it lives |
| --- | --- | --- | --- |
| 1 | Architecture, models, secrets in env only | done | `config/`, `apps/*` |
| 2 | Auth (login for the hosted app) | done | `/accounts/login/` — on when `DJANGO_DEBUG=False` |
| 3 | Shopify client + incremental sync | done | `apps/integrations/shopify/` |
| 4 | Inkthreadable client + sync | done | `apps/integrations/inkthreadable/` |
| 5 | Product / variant mapping UI | done | `/mapping/`, `/mapping/blanks/` |
| 6 | Monzo CSV import, idempotent | done | CLI + (upload on Data health) |
| 7 | Historical spreadsheet import, non-destructive | done | `import_finance_dashboard` — workbook is never written |
| 8 | Deterministic finance engine (cash P&L) | done | `apps/analytics/cashflow.py` |
| 9 | Order profitability breakdown | done | `/orders/<id>/` |
| 10 | Product analytics (units, mix, period) | done | `/products/`, `/products/<id>/` |
| 11 | Dashboard with date ranges + charts | done | Cash line + columns; custom From/To dates |
| 12 | Reconciliation Shopify → payouts → Monzo | done | `/reconciliation/` |
| 13 | What-if simulations | done | `/simulate/` — does not write the books |
| 14 | Business insights page (numbered, not invented) | done | `/insights/` — each line cites a calculated number |
| 15 | Customer analytics | done | `/customers/` — new vs returning, repeat rate, AOV |
| 16 | Shipping analytics | done | `/shipping/` — charged vs cost, free-postage impact |
| 17 | Discount analytics | done | `/discounts/` — codes, revenue given away, profit only when cost is known |
| 18 | Refund analytics | done | `/refunds/` — rate by product / variant; cash is the refund total |
| 19 | Product groups (tees, hoodies, …) | done | `/groups/` — title-matched (Shopify types are empty) |
| 20 | AI assistant (explains calculated numbers only) | skipped | Exports instead — paste CSV into any AI if wanted |
| 21 | Render deployment | partial | Free web blueprint; Postgres URL set in the dashboard — not live yet |

---

## Hard rules (never regress)

- [x] Historical spreadsheet is never overwritten
- [x] Secrets only in environment variables; `.env` gitignored
- [x] Money is `Decimal`, 2dp; bank amounts signed (in +, out −)
- [x] Unknown cost / unmatched cash displays as `—`, never silently £0
- [x] Confirmed mappings (and cash links) are not overwritten by autosuggest
- [x] Finance calculations live in the backend, not in templates or an AI
- [x] Shopify sales ≠ bank cash; the two are reconciled, not collapsed
- [x] Incremental build; each stage tested before the next

---

## Extra constraints (13 Sep 2026)

### Interface

- [x] Page / section navigation must not look like period or list filters
- [x] Two visible sections: **Finance** (spreadsheet) and **Insights** (shop)
- [x] StayLit branding (wordmark / colours inspired by [staylitapparel.co.uk](https://www.staylitapparel.co.uk/))
- [x] Real logos: 60×60 `static/web/logo.avif` (from `black_logo_small.avif`); original white/black/blue palette (not cream/gold)
- [x] Cash toggle for profit and expenses excluding salary & tithe (spreadsheet default)
- [x] Product/order sort headers; hide £0 free downloads and 0-order products by default
- [x] Shopify Cost per item synced as a fallback cost; never invented as £0
- [x] Bank CSVs already match the live books (2583 rows; 1 known Monzo duplicate skipped)
- [x] Useful charts: profit over time as a line/area, not only column bars
- [x] Period presets: today, 7/30/90d, month, quarter, YTD, last year, all time
- [x] Operator guide at `/guide/` (info button next to Admin; no terminal steps)
- [x] All monthly updates from Data health (Shopify, Inkthreadable, linking, uploads)
- [x] Change password in the app header (and Django Admin → Users)

### Login and Render

- [x] Login required on the hosted (Render) app
- [x] Health check stays public (`/healthz`)
- [ ] Postgres + env secrets on Render; `DJANGO_DEBUG=False` (blueprint ready, not live)

### Monzo import

- [x] Dedup by Monzo transaction ID (`tx_…` / `mm_…`); overlapping months do not double-count
- [x] Upload a new month’s CSV from the UI (e.g. October file that also contains August rows)
- [x] Existing rows, including hand-set categories, stay untouched

### Bank-first finance

- [x] Revenue, expenses and profit on Cash always come from Monzo, even if Shopify/Inkthreadable are empty or stale
- [x] Product profit is withheld when a sold line has no cost

### Inkthreadable catalogue churn

- [x] Blanks can be marked / shown as phased out; old costs are kept
- [x] Historical `SupplierVariantCost` is append-only (order date lookup)
- [x] Missing catalogue SKU (e.g. AT002) is left unmapped, not forced onto another garment

---

## Brief coverage (original prompt)

### Shopify

- [x] Products, variants, orders, customers, refunds, discounts, shipping, tax, fees
- [x] Shopify IDs preserved; incremental sync
- [ ] Inventory (only if useful — not started)

### Inkthreadable

- [x] Orders, costs from line items, shipping, tracking when present
- [x] Mapping Shopify → blank → cost
- [ ] Full current catalogue (API has no public catalogue list)
- [x] Recent orders (sync through Sep 2026; Shopify links via `#1440/fulfilment-id`)

### Finance / dashboard

- [x] Categorisation rules + manual lock
- [x] Layered P&L: contribution → operating → net
- [x] Spend mix
- [x] Vs last year on cash cards
- [x] Custom date range picker (`start=` / `end=` plus presets)
- [x] Category-rule editor in the app UI (`/categories/`)

### Order + product

- [x] Full order contribution breakdown
- [x] Product: orders, units, revenue, costs, fees, profit, margin, size/colour mix
- [x] Periods on product pages
- [x] Previous-period comparison on product pages (cash has vs last year)
- [x] Group analytics UI

### Reconciliation

- [x] Expected cash (sales − fees) vs Stripe/Adyen
- [x] Matched / unmatched days and payouts
- [x] Inkthreadable charges vs invoices when dates/amounts overlap
- [x] Confirm / clear; confirmed not overwritten

### Simulations (this stage)

- [x] Price change, optional volume change
- [x] Supplier cost change
- [x] Free / changed shipping + break-even volume
- [x] Discount %
- [x] Clearly labelled assumptions; does not write real books

### Insights / AI / customers / shipping / discounts / refunds

- [x] Dedicated insights page with numbers behind each line
- [x] Cash uses the corrected (kind-based) figures; LEGACY mode remains in the engine for validation only
- [x] Wix-era bank (before 16 Jul 2025) is not treated as Shopify
- [x] Customer analytics: new / returning, repeat rate, AOV (paid orders; guests without email stay unidentified)
- [x] Shipping: charged vs known Inkthreadable cost; free postage on physical orders only
- [x] Discounts: codes, revenue given away, profit impact only on complete-cost orders
- [x] Refunds: rate by product / variant on sales in the period; cash is the refund total
- [x] Product groups: hoodies / tees / sweatshirts from titles; `/groups/`
- [x] CSV downloads of the tables already on each page (for records or an AI of the user's choice)
- [ ] AI assistant in the app — skipped; not required before Render

---

## How to use this file

When a stage ships, mark it `done` here **and** in the README roadmap.
If something is only half-true (e.g. login exists in admin but not on the app), keep it `partial`.
Do not tick bank-independent finance as done on product pages — those pages need mappings.
Do not tick Render as done until the service is actually deployed.
