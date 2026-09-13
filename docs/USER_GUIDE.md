# StayLit Apparel — how to use this app

This is the private business manager for StayLit Apparel. It replaces copying bank files into the old Finance Dashboard spreadsheet.

There are two halves:

- **Finance** — what actually moved in Monzo (Cash, Reconciliation, Categories, Data health).
- **Insights** — what Shopify sold and what Inkthreadable charged (products, orders, shipping, and the rest).

Those two are related, but they are not the same number. A sale is booked on the day the customer places it. The money lands in Monzo days later, after card fees. Use **Reconciliation** to join them up. Do not expect Cash and Orders to match pound-for-pound.

---

## Sign in

Open [https://staylit-business-manager.onrender.com](https://staylit-business-manager.onrender.com).

The first click after a quiet spell can take a minute. The free host goes to sleep; that is normal, not a broken login.

- **Username:** `laura`
- **First password:** `1234`

Change that password the first time you are in: **Change password** (top right), next to Admin. You can also change it in **Admin → Users → laura**. The new password needs letters in it — a number-only password will be refused.

- **i** (next to Admin) opens this guide.
- **Log out** ends the session.

This is a private site. Do not share the login.

---

## First-time setup (new hosted copy)

If Cash is empty, the books have not been loaded yet. Stay on **Data health** and work top to bottom. You do not need a computer terminal.

1. **Import bank CSV** — upload a Monzo export. Overlapping months are fine.
2. Or **Import spreadsheet** — upload the Finance Dashboard `.xlsx` once, for older history. The spreadsheet itself is never changed.
3. **Update Shopify** — pulls products and orders. Usually a few minutes. Leave the page open.
4. **Update Inkthreadable** — pulls what the printer charged. The first run can take a long time. Leave the page open; it refreshes itself.
5. **Link supplier orders** — attaches printer jobs to Shopify orders so profit and postage can be shown.
6. **Suggest product mappings** — then open **Mapping** and confirm what looks right. Mark free downloads as digital.
7. **Suggest bank matches** — then open **Reconciliation** and confirm Stripe and Inkthreadable rows you agree with.

Yellow notes mean something still needs a person. The app will not invent a cost or a match.

---

## Dates

Most pages have period pills (Today, Last 7 days, This month, Year to date, and so on) plus **From / To** dates.

- Use a pill for a named window.
- Type two dates and **Apply dates** for any other range.
- Product pages also show **vs previous** — the same length of time immediately before the window you picked.

Cash compares to the **same dates last year**, because that is how the old dashboard worked.

Shopify launched on **16 July 2025** (order `#1001`). Bank rows before that are from the Wix era. They still belong on Cash. They will not match Shopify payouts, and that is expected.

---

## Download tables

Every list page has **Download this table (CSV)**. Open the file in Excel or Numbers, keep it as a record, or paste it into an AI tool of your choice if you want a written summary.

The numbers in the file are the same numbers on the page. An AI cannot invent a profit the app refused to show.

---

## Finance

### Cash

This is the P&L from the bank. Revenue, expenses and profit always come from Monzo, even if Shopify is stale.

Default view **excludes salary and tithe**. Use **Include salary & tithe** when you want the full net.

A dash (—) means the figure cannot be stated. The app will not pretend a missing cost is £0.

If a yellow note says transactions have no category, open **Categories**.

### Categories

Rules look at the bank text (for example `INKTHREADABLE` or `SHOPIFY`) and assign a category.

- Lowest **priority** number is tried first. First match wins.
- **Direction** matters: Shopify payouts and the Shopify subscription both say “SHOPIFY”. Money in vs money out is what separates them.
- **Apply rules** fills gaps. It never overwrites a category you set by hand, or one imported from the spreadsheet.

### Reconciliation

Matches:

- Shopify sales days → Stripe / Adyen deposits
- Inkthreadable invoices → Inkthreadable bank charges

Confirm what looks right. Clear a suggestion if it is wrong. Confirmed links are not overwritten the next time suggestions run.

### Data health

This is the only place you need for monthly updates:

1. Upload the latest Monzo CSV.
2. **Update Shopify**.
3. **Update Inkthreadable**.
4. **Link supplier orders**.
5. Glance at **Mapping** and **Reconciliation**.

---

## Insights

### Insights

Numbered findings from figures the rest of the app already calculated. Nothing here is a forecast. Download the list if you want to keep it or drop it into another tool.

The list is rebuilt every time you open the page or change the dates. There is no overnight job. After you upload a new Monzo file or run **Update Shopify** / **Update Inkthreadable** on Data health, come back here and the wording will follow the new numbers.

### Products, Groups, Orders

Units, net sales, contribution profit and margin for what sold in the period.

Profit stays **—** until every sold line has a cost. That usually means:

1. The Shopify order is linked to its Inkthreadable fulfilment (Data health → Link supplier orders), **or**
2. The variant is mapped on **Mapping** (or has a Shopify Cost per item).

Free Bible-verse downloads and other £0 products are hidden by default. They are not missing prices.

**Groups** (T-Shirts, Hoodies, Sweatshirts, Digital) are matched from product titles.

Open an order for the full breakdown. Refunded garments still cost money — print-on-demand does not get the shirt back.

### Customers, Shipping, Discounts, Refunds

- **Customers** — new vs returning, repeat rate, average order value. Guests with no email stay unidentified.
- **Shipping** — what the customer paid vs what Inkthreadable charged, when that link exists.
- **Discounts** — codes and automatic discounts. Profit impact only on orders whose costs are complete.
- **Refunds** — rate by product and variant. The cash figure is the refund total.

### Mapping and Blanks

Shopify names a hoodie one way; Inkthreadable names the blank another way. Mapping is how the app knows what a sale cost when there is no linked fulfilment.

- Confirm suggestions you trust.
- Mark lead-magnet downloads as **digital**.
- Leave a missing blank unmapped rather than forcing it onto the wrong garment.

### Simulate

What-if only. It does **not** write the books. Read the listed assumptions.

---

## Each month

1. Export the latest Monzo CSV and upload it on **Data health**.
2. **Update Shopify**, then **Update Inkthreadable**, then **Link supplier orders**.
3. Confirm new rows on **Mapping** and **Reconciliation**.
4. Read **Cash** and **Insights**.
5. Download any table you want to keep.

---

## What the symbols mean

| You see | It means |
| --- | --- |
| **—** | The app does not have enough to state a number. Not zero. |
| **actual** | Taken from Inkthreadable or the payment provider. |
| **from price list** | Catalogue cost on the order date. |
| **from Shopify** | Cost per item entered in Shopify. Used only when there is no supplier cost. |
| **estimated** | A stated assumption (usually card fees). |
| Yellow note | Something needs a person. |

---

## What this app will not do

- Invent a cost, a bank match, or a supplier link.
- Treat Wix-era bank rows as Shopify.
- Count a supplier refund as sales income.
- Overwrite the Finance Dashboard workbook.
- Write a profit figure when a cost is missing.
