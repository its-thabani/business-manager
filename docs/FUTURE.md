# StayLit Apparel — future improvements

Ideas parked after the first live ship (Render + Neon, 13 Sep 2026).
Implement from here when you want the next slice. Do not treat this as a
commitment or a second brief.

When something ships: tick it, move the line into `REQUIREMENTS.md`, and
delete or strike it here so this file stays a backlog, not a second
checklist of done work.

The hard rules still apply: never invent missing costs, never overwrite the
Finance Dashboard, never collapse Shopify into bank cash, never let
autosuggest overwrite a confirmed map or cash link.

---

## Do next (small, already felt)

These remove leftover friction. Prefer them over new pages that restate Cash.

### ~~Recategorise a bank row in the app~~

Shipped: category dropdown on each Expenses row (locks as a manual choice).
**Apply rules** updates spreadsheet labels when a rule matches; hand-set
categories stay locked.

### “Unlock profit” / uncategorised queue

One worklist instead of hunting across pages:

- Bank rows with no category
- Shopify variants whose profit is still **—**
- Unlinked Inkthreadable jobs

Insights already hints at withheld profit. This would turn it into “do these
five things and the numbers complete.”

### Cost-changed warning

When a newer Inkthreadable price exists than the mapped / stored cost, flag
the variant. Simulate can model a rise; this would say it already happened.

### Direct / personal sales

A tiny form: date, amount, note → books as **Direct Sales Income** (locked).
Stops “paid to my account, transferred in” being treated as Salary again.
Do not invent a Shopify order for it.

---

## Once the monthly ritual hurts

### Tax-year pack

One download for the accountant: income, expenses by category, drawings,
refunds, date range = UK tax year. The figures already exist; this is
packaging.

### Monthly email or PDF

Profit, top expenses, uncategorised count, cash gap. Means she does not have
to remember a sleeping Render tab.

### Scheduled Shopify + Inkthreadable pull

Data health buttons work. A weekly job (and/or a cheap keep-alive so Render
does not sleep) is the real monthly-ritual win.

Monzo API later — the CSV upload is already safe and idempotent. Do not
replace it until the API path is as careful about existing categories.

---

## Only if the business changes

| Idea | When it becomes useful |
| --- | --- |
| Shopify inventory | You hold stock. POD blanks live at the printer. |
| Ads vs sales | Serious Meta/Google spend and you want ROAS, not just a bank category. |
| Repeat-buyer CRM / “not bought in 90 days” | You start emailing lapsed customers. `/customers/` is enough until then. |
| In-app AI | Still skip. CSVs plus the operator guide are safer than a chat that might invent £0. |
| Full Inkthreadable catalogue | Their API still has no public catalogue list. Blanks come from orders. |
| Paid Render / keep-alive | First click after idle is too slow to live with. Infra, not a product page. |

---

## Do not build

- Another dashboard that restates Cash
- Forecasts or invented “runway”
- Multi-user roles
- Auto-matching that overwrites confirmed maps or cash links
- Treating Shopify totals as bank cash
- Forcing unmapped SKUs (e.g. AT002) onto another garment
- Writing anything back to `files/Finance Dashboard.xlsx`

---

## How to pick the next slice

1. Recategorise-in-app + unlock-profit queue (highest leftover friction).
2. Cost-changed warning and/or direct-sales form if those keep biting.
3. Tax-year pack or a monthly digest when an accountant or a forgotten tab
   is the pain.
4. Everything in “only if the business changes” stays parked until it does.
