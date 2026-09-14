# StayLit Apparel — how to use this app

This is the private business manager for StayLit Apparel. It replaces copying bank files into the old Finance Dashboard spreadsheet.

There are two halves. They are related, but they are **not the same number**.

- **Finance** — what actually moved in Monzo (Cash, Expenses, Reconciliation, Categories, Data health).
- **Insights** — what Shopify sold and what Inkthreadable charged (products, orders, shipping, and the rest).

A sale is booked on the day the customer orders. The money lands in Monzo days later, after card fees. Use **Reconciliation** to join them up. Do not expect Cash and Orders to match pound-for-pound.

The **i** button (top right) opens this guide.

---

## Sign in

Open [https://staylit-business-manager.onrender.com](https://staylit-business-manager.onrender.com).

The first click after a quiet spell can take a minute. The free host goes to sleep; that is normal, not a broken login.

- **Username:** `laura`
- **First password:** `1234`

Change that password the first time you are in: **Change password** (top right). You can also change it in **Admin → Users → laura**. The new password needs letters in it — a number-only password will be refused.

- **Log out** ends the session.
- **Admin** is the technical back office (individual bank rows, users). Day to day you should not need it.

This is a private site. Do not share the login.

---

## Dates (almost every page)

Most pages have period pills plus **From / To**.

| Pill | Meaning |
| --- | --- |
| Today | Calendar today (London) |
| Last 7 / 30 / 90 days | Rolling windows |
| This month / Last month | Calendar months |
| This quarter | Current quarter |
| Year to date | 1 January through today |
| Last calendar year | The previous full year |
| Last 12 months | Rolling year |
| All time | Everything in the books |

Type two dates and **Apply dates** for any other range.

- **Cash** compares to the **same dates last year** (how the old dashboard worked).
- **Product** pages also show **vs previous** — the same length of time immediately before the window you picked.

Shopify launched on **16 July 2025** (order `#1001`). Bank rows before that are from the Wix era. They still belong on Cash and Expenses. They will not match Shopify payouts, and that is expected.

---

## Download tables

Every list page has **Download this table (CSV)**. Open it in Excel or Numbers, keep it as a record, or paste it into another tool if you want a written summary.

The numbers in the file are the same numbers on the page. A chat tool cannot invent a profit this app refused to show.

---

## Finance

### Cash

**What it is for:** “Is the business making money from the bank?” This is the closest replacement for the old dashboard totals.

**What you see**

- Cards for **Revenue**, **Expenses**, **Profit**, and how many bank rows sit in the period.
- A line chart of profit and revenue over time.
- Monthly columns (revenue / expenses / profit).
- “Where the money went” — the largest categories.
- A full profit-and-loss table: contribution → operating → net.
- Every **reportable** category with a total and share.

**What you can do**

- Change the dates.
- Toggle **Profit / expenses excluding salary & tithe** (the spreadsheet default) vs **Include salary & tithe**.
- Sort the category table by name, treatment, count, total or share.
- Download the P&L and category table.

**What it can tell you**

- Cash profit this month / year, and vs the same dates last year.
- Whether spend is printer cost, postage, fees, software, or drawings.
- Whether uncategorised rows are quietly left out of the figures (yellow note).

Revenue, expenses and profit on this page **always come from Monzo**, even if Shopify is stale.

Transfers and anything marked excluded do not appear here. They are outside the books.

A dash (**—**) means the figure cannot be stated. The app will not pretend a missing cost is £0.

Money paid to a personal account and then transferred in (for example a t-shirt paid to Laura) should be **Direct Sales Income**, not Salary. Salary credits are not treated as sales.

---

### Expenses

**What it is for:** the old Expense Summary — every true outgoing in a period, not just the P&L total.

**What you see**

- **True expenses** — money that left the account and counts as a cost.
- **Customer refunds** as a separate card (they reduce sales; they are not added to expenses).
- Uncategorised outgoings (shown so you can label them; not in the total).
- A bar chart and table of spend by category.
- A row for every reportable outgoing (date, who, category, amount).

**What you can do**

- Pick any dates.
- **All true expenses** includes salary and tithe (they did leave the account).
- **Hide salary & tithe** for operating spend only.
- Sort the outgoing list by date, name, category or amount.
- Download every outgoing row.

**What it can tell you**

- Exactly where cash went in a month.
- Whether Inkthreadable, Shopify hosting, or software is the large line.
- Which individual payments made up a category.

Transfers and excluded rows are not listed.

---

### Reconciliation

**What it is for:** joining shop activity to bank cash. Shopify sales and Monzo deposits are different clocks.

**What you see**

- Shopify net sales vs expected cash after fees vs Stripe/Adyen received, and the **cash gap**.
- Inkthreadable leaving the bank vs supplier invoices vs estimated fulfilment.
- How many payouts and sales days are matched or still open.
- A monthly chart of expected cash, bank payouts and printer spend.
- Suggested matches you can confirm or clear.

**What you can do**

- Pick dates (Wix-era bank before 16 Jul 2025 will not match Shopify — that is noted).
- **Confirm** a suggested Shopify-day → Stripe/Adyen match.
- **Confirm** an Inkthreadable invoice → bank charge.
- **Clear** a suggestion that is wrong.
- Run **Suggest bank matches** from Data health first if the list is empty.

Confirmed links are **not** overwritten the next time suggestions run.

**What it can tell you**

- Whether this month’s sales have actually landed.
- Whether the printer has been paid in line with invoices.
- Which deposits or charges still need a person.

---

### Categories

**What it is for:** teaching the app what a Monzo row *means*, so Cash and Expenses stay correct.

**What you see**

- The list of rules (pattern, what they look at, which category they assign, priority).
- A form to add a rule.
- A count of rows that still have no category.

**What you can do**

- Add a rule: pattern (for example `INKTHREADABLE`), match type, which text to look at, money in / money out / either, category, priority, optional name.
- Click a rule to edit it.
- **Apply rules** — fills gaps only. It never overwrites a category you set by hand, or one imported from the spreadsheet.

**How rules work**

- Lowest **priority** number is tried first. The first match wins.
- Matching ignores case and extra spaces.
- **Direction** matters: Shopify payouts and the Shopify subscription both say “SHOPIFY”. Money in vs money out is what separates revenue from hosting.

To put one row in a category by hand (and lock it): **Admin → Bank transactions**.

---

### Data health

**What it is for:** keeping the books current. This is the only page you need for monthly updates. You do not need a computer terminal.

**What you can do**

| Action | What it does |
| --- | --- |
| **Import bank CSV** | Upload a Monzo export. Overlapping months are fine. Rows already held are left alone, including categories you set by hand. |
| **Import spreadsheet** | Read the Finance Dashboard `.xlsx` once for older history. The file is never overwritten. |
| **Update Shopify** | Pull products and orders. Usually a few minutes. |
| **Update Inkthreadable** | Pull printer fulfilments and costs. The first run can take a long time. |
| **Link supplier orders** | Attach Inkthreadable jobs to Shopify orders using the saved refs. Also rebuilds Blanks from those invoices (AT002 / AWDis 180). Does not call the API. Wix / Etsy / website jobs stay unmatched. |
| **Suggest product mappings** | Guess Shopify → blank matches. Confirmed mappings are left alone. |
| **Suggest bank matches** | Suggest Stripe and Inkthreadable links. Confirmed links are left alone. |

While Shopify or Inkthreadable is running, the page refreshes every 15 seconds and the Update buttons stay disabled. Leave the tab open.

The table at the bottom shows last update time and record counts. Yellow notes need a person.

The live site already has history loaded. Do not run a “first time” sync just to set up. Use these buttons for **new** months and **new** orders.

---

## Insights

### Insights

**What it is for:** a numbered briefing from figures the rest of the app already calculated. Nothing here is a forecast.

**What you see**

Typical lines (only those the data can support for the dates you picked):

- Cash profit from Monzo (not Shopify).
- Shopify sales vs bank cash (different clocks).
- Paid orders vs free downloads.
- Postage charged vs printer postage.
- Discounts given away.
- Refunds in the period.
- Best seller and concentration in a few products.
- Whether shop profit is incomplete, and an estimate from the lines that have a cost.
- Stripe in vs Inkthreadable out.
- Repeat buyers.
- How many Shopify days are matched to Stripe.
- Which garment group is carrying sales.
- Products that lost money after costs.
- Card fees.
- Destination country.

**What you can do**

- Change the dates; the list is rebuilt.
- Follow the “Open …” link under a finding.
- Download the list as CSV.

There is no overnight job and no extra refresh button. After you import a bank file or update the shop, open this page again.

---

### Products

**What it is for:** which garments sold, and whether they made contribution profit.

**What you see**

- Shop totals: units, net sales, contribution profit, cost coverage.
- A table of products (sortable).
- Free £0 downloads and products with no orders hidden by default (toggles at the top).

**What you can do**

- Change dates; sort by name, units, orders, revenue, profit or margin.
- Open a product for the detail page.
- Download the table.

**What it can tell you**

- What is actually selling in this window.
- Which lines have no cost yet.
- Whether a best seller is profitable once the printer is paid.

A **green or red** profit with a complete pill is the real total — every sold line has a cost. A **~** figure labelled **approx.** (dashed card) is the margin of the items that already have a cost, applied to the rest. That is not the books.

A product or variant still showing **—** has no cost of its own. Click **add cost** to open Mapping for that product. Do not read a group approximation as that line’s profit.

To replace an approximation with a real total: map the variant on **Mapping**, run **Link supplier orders** on Data health, or enter **Cost per item** on the Shopify variant.

---

### Product detail (click a product)

**What it is for:** one garment in the period you picked.

**What you see**

- The product name in the heading (and the browser tab).
- Units, net sales, contribution profit, refund rate, each vs the previous window of the same length.
- Monthly sales chart.
- Size mix and colour mix of what sold.
- Variants that sold, with profit when cost is known.
- The full catalogue of variants (size, colour, SKU, price).
- Links back to Products and to Mapping.

**What it can tell you**

- Whether XS is the size that sells.
- Whether a colour is returning more than others.
- Whether this hoodie is carrying a complete profit, an **approx.** total, or still **—**.

---

### Groups

**What it is for:** tees vs hoodies vs sweatshirts vs digital, from product titles (Shopify’s product type is empty).

**What you see**

- The same style of totals as Products, per group.
- A table of groups; click through for the products in that group.
- **~ approx.** profit / margin when only some items in the group have a printer cost. Complete groups stay green or red.

**What it can tell you**

- Which garment family is carrying the shop.
- A rough tee vs hoodie margin before every mapping is finished (labelled estimate).
- Where to add a cost so the estimate becomes a real total (Mapping, Data health, or Shopify Cost per item).

---

### Customers

**What it is for:** who bought, who is new, who came back.

**What you see**

- Counts of identified buyers, new vs returning, repeat rate, average order value.
- A table of customers (orders, spend).
- Click a customer for their orders in the period.

**What you can do**

- Paid orders only (default) vs including £0 downloads.
- Change dates; sort; download.

**What it can tell you**

- Whether the shop is mostly one-off buyers.
- Who the repeat customers are.

Guests with no email stay **unidentified** and are left out of rates rather than guessed.

---

### Orders

**What it is for:** every countable Shopify order, with profit when costs are known.

**What you see**

- A sortable list: name, date, customer, status, total, profit.
- £0 digital downloads hidden by default.

**Click an order** for the full breakdown:

- Product revenue, postage charged, discounts, refunds, tax.
- Inkthreadable product cost and postage (or **—**).
- Card fees (actual or estimated — it says which).
- Contribution profit.
- Line items and any refunds.

**What it can tell you**

- Whether a specific order made money.
- Why profit is **—** (unlinked printer job or unmapped variant).

Refunded garments still cost money. Print-on-demand does not get the shirt back.

---

### Shipping

**What it is for:** what customers paid for postage vs what Inkthreadable charged.

**What you see**

- Charged, supplier cost (when known), postage result.
- Free-postage impact on physical orders only.
- Mix by destination country.
- Per-order charged vs cost.

**What you can do**

- Filter to free postage only.
- Change dates; download.

**What it can tell you**

- Whether “free UK delivery” is eating contribution.
- Which countries you ship to, and what they paid.

Cost is **—** until the fulfilment is linked.

---

### Discounts

**What it is for:** codes and automatic discounts — what was given away.

**What you see**

- Total discounted, share of list price.
- Profit impact **only** on orders whose costs are complete (otherwise **—**).
- Ranked codes, and the orders that used them.

**What it can tell you**

- Which code is actually costing margin.
- Whether automatic discounts are larger than named codes.

---

### Refunds

**What it is for:** what came back, on sales in the period you picked.

**What you see**

- Refunded amount and rate (of what customers paid / of units).
- Ranked products and variants.
- Individual refund events.

**What you can do**

- Merchandise only vs including digital.
- Restrict to one product; download.

**What it can tell you**

- Whether one SKU is coming back more than others.
- The cash total of refunds (that is the cash figure — not a guess).

---

### Simulate

**What it is for:** “what if I change the price / blank cost / postage / discount?” It does **not** write the books. It replays **actual orders** in the period you picked.

**What you can do**

- Pick dates and optionally one product (or the whole shop).
- Set a new unit price, or a £ price change.
- Optionally change volume (%).
- Set a new supplier cost, or a £ / % cost change.
- Add an extra discount (%).
- Tick **Free shipping**.
- **Run scenario**.

**What you see**

- A yellow list of every assumption used.
- Revenue now vs if; profit now vs if (profit stays **—** if costs are incomplete).
- A comparison chart.
- Break-even volume when you change shipping.

**What it can tell you**

- Whether a £2 price rise covers a dearer blank.
- What free postage would have done to last month’s real orders.
- How much volume you would need to break even on a postage change.

Bookmark the URL to keep a scenario. Nothing is saved into the books.

---

### Mapping

**What it is for:** telling the app which Shopify product is printed on which Inkthreadable garment (the “blank”), so profit can be calculated when there is no linked fulfilment — and as a fallback when there is.

**How it works, in order**

1. Inkthreadable invoices already in the app become rows on **Blanks** (JH001 hoodie, AT002 tee, …).
2. On **Mapping**, each Shopify product is assigned one of those blanks.
3. Each size/colour is matched to an Inkthreadable SKU (for example `AT002-DBL-L`) so the unit cost is the price they actually charged.

**The AWDis 180 t-shirt (most StayLit tees)**

That garment is Inkthreadable code **AT002**: [The AWDis 180 T-shirt](https://www.inkthreadable.co.uk/the-awdis-180-t-shirt). It does **not** appear on Blanks until invoices have been turned into catalogue rows. Inkthreadable puts the Shopify design name on the line (“Perfect Peace Graphic T-Shirt”) and the real blank in the SKU (`AT002-…`).

To get it in:

1. **Data health** → **Link supplier orders**. That rebuilds blanks from stored invoices. You should then see **The AWDis 180 T-shirt** / **AT002** on Blanks.
2. **Data health** → **Suggest product mappings** (or the same button on Mapping). Confirmed maps stay put.
3. Open **Mapping**, filter **Needs review**, and confirm the tees that now say AT002.

Do not map those tees onto a hoodie or a Stanley/Stella Rocker just because that blank is in the list. If AT002 is still missing after step 1, add it on Blanks (name `The AWDis 180 T-shirt`, code `AT002`, brand `AWDis`), assign it on a product, then **Create missing variants** with the unit cost from an Inkthreadable invoice (not a guess).

**What you see**

- How many sold units have a cost vs still unknown.
- How many products need review.
- How many are marked digital (cost is £0 on purpose).
- A table of products and mapping status.

**What you can do**

- Filter: needs review / all / digital / confirmed.
- **Suggest mappings** (safe to re-run; confirmed matches stay).
- Open a product to confirm a suggested blank, pick a blank, mark **digital / no supplier**, or leave it unmapped.
- Map individual variants when sizes differ.
- Create a blank on the product page if it has never been invoiced.

**What it can tell you**

- Why Products still shows **—** on profit.
- Which lead magnets should be digital so they stop looking like missing prices.

---

### Blanks

**What it is for:** the Inkthreadable garments this shop has been charged for, grouped by product code (AT002, JH001, STTU758, …). There is no public catalogue API.

**What you see**

- Each blank, code, brand, variant count, how many shop products use it.
- **Phased out** when Inkthreadable stopped using it. Old costs are kept.

**What you can do**

- Search by name or code (try `AT002` or `180`).
- Open a blank to see variants, historic unit costs, and which shop products use it.
- Add a blank that has never been invoiced, then assign it on Mapping and set a unit cost.
- Rebuild from invoices via Data health → Link supplier orders.

**What it can tell you**

- What you have actually been charged for over time.
- That a discontinued hoodie can still explain last year’s profit.

---

## Header extras

| Control | What it does |
| --- | --- |
| **i** | This guide |
| **Change password** | Set a new login (needs letters) |
| **Admin** | Technical lists: bank rows, users, imports |
| **Log out** | End the session |

In Admin you can open one Monzo row and set its category by hand (tick **category locked**). Use that for one-off corrections such as a personal-account t-shirt transfer that must be **Direct Sales Income**.

---

## Each month

1. Export the latest Monzo CSV and upload it on **Data health**.
2. **Update Shopify**, then **Update Inkthreadable**, then **Link supplier orders** (rebuilds blanks such as AT002). Leave Data health open while an update runs.
3. **Suggest product mappings**, then confirm new rows on **Mapping** and **Reconciliation**.
4. Read **Cash**, **Expenses** and **Insights**.
5. Download any table you want to keep.

---

## First-time setup (only if Cash is empty)

The hosted copy already has history. If you ever stand up a blank database:

1. Import bank CSV (or the Finance Dashboard `.xlsx` once).
2. Update Shopify, then Inkthreadable.
3. Link supplier orders.
4. Suggest mappings; mark digital downloads.
5. Suggest bank matches; confirm what you agree with.

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
- Count a locked Salary credit as a sale (unless you recategorise it as Direct Sales Income).
- Show excluded or transfer categories on Cash, Expenses or Insights.
- Overwrite the Finance Dashboard workbook.
- Write a profit figure when a cost is missing.
- Change real books from **Simulate**.
