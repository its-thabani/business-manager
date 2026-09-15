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

### Printer VAT and “checkout” cost (Laura, 15 Sep 2026)

She prices a garment as print + shipping + VAT (and +£4.20 before tax for a
second print). The app uses Inkthreadable **net** print + postage, and
**excludes** VAT the customer paid. Linked invoices store tax but contribution
profit does not add it. Next slice: optionally cost from `total` (VAT-inclusive)
on linked jobs, and a double-sided / neck-label SKU on mapping.

### Exchanges, sale reprints, already-printed stock

A size swap is paid for twice at the printer; the original often sells later
on sale. The app sees a refund, a new order, and sometimes a Shopify discount
workaround (#1332). No first-class exchange or “already printed / shipping
only” product type yet. Until then: mark sale listings digital or override
cost; do not expect Shipping −£ rows to pair with the original order.

### Bulk mapping and live listings only

Same blank (AT002 / JH001) on every tee/hoodie. Confirm-all-of-this-blank
would save clicks. Mapping still lists archived products that sold, so a cost
can be mapped. Products hides archived listings by default. Never-sold drafts
stay off Mapping unless you ask.

### Manual cash match

Unmatched sales days / payouts / Ink charges are wait-lists. A person picking
“this payout is those three days” is the missing control.

### Insights: best size sold vs returned

Product detail already has size mix of what sold; Refunds can be filtered to
one product. A shop-wide size sold-vs-returned line on Insights is still open.

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

1. Unlock-profit queue, then printer-VAT / double-sided cost if margins still
   feel “too good”.
2. Exchanges / already-printed stock if sale listings keep distorting profit.
3. Tax-year pack or a monthly digest when an accountant or a forgotten tab
   is the pain.
4. Everything in “only if the business changes” stays parked until it does.
