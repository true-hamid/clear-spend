# Output files

**Implementation**: `app/excel_builder.py`, invoked from
`app/main.py:upload` after all uploaded PDFs are parsed and matched.

Processing a batch of statements produces up to two downloadable files:

1. **The processed workbook** (`.xlsx`, `build_workbook`) — always produced.
   Every parsed transaction, matched or not, on the `Transactions` tab (see
   "Tabs" below).
2. **The Needs Review CSV** (`.csv`, `build_review_csv`) — only produced
   when at least one transaction in the run went unmatched. Two columns,
   `Original Description | Type` (`Credit`/`Debit`), one row per
   unrecognized transaction. This replaces the old workbook-embedded "Needs
   Review" tab. The summary page (`app/templates/summary.html`) tells the
   user to hand this file to an admin so the unknown merchants can be added
   to `merchant_mapping` for better matching on future uploads — see
   `docs/mapping.md` and `docs/unrecognized_merchants.md`.

## Tabs

1. **Transactions** — every parsed row from every uploaded PDF, combined.
   Columns: `Transaction Date | Posting Date | Original Description |
   Cleaned Merchant | Category | Type | Amount (AED) | Source File |
   Possible Duplicate`.
   - Header row: bold white text on a solid fill (`HEADER_FILL`/`HEADER_FONT`
     in `app/excel_builder.py`), frozen (`freeze_panes = "A2"`), autofilter
     over the full used range.
   - `Amount (AED)` uses number format `#,##0.00;[RED](#,##0.00)`.
   - Credits (`is_credit=True`, i.e. a trailing `CR` on the statement line)
     are stored as **negative** amounts with `Type = Credit`; debits are
     positive with `Type = Debit`.
   - `Source File` is the uploaded filename with its extension stripped
     (`Path(r.source_file).stem`), e.g. `sample_statement.pdf` becomes
     `sample_statement`.
2. **Category Summary** — one row per distinct category present in this
   run, `Category | Total Amount (AED) | # Transactions`, computed with
   live `=SUMIF(...)` / `=COUNTIF(...)` formulas referencing the
   `Transactions` tab (not pre-computed Python values), plus a bold
   `TOTAL` row summing both columns. Recalculates automatically if rows
   on the Transactions tab are edited after download.
   - `Payments & Transfers` (payments toward a *previous* statement's
     balance, not new spending) is excluded from this tab entirely —
     `CATEGORIES_EXCLUDED_FROM_SUMMARY` in `app/excel_builder.py` — so it
     doesn't net a large credit against real category totals or skew
     `TOTAL`. Rows in that category still appear on the Transactions tab.
3. **Merchant Summary** — the same idea as Category Summary, but grouped by
   `Cleaned Merchant` instead of `Category`: one row per distinct merchant
   present in this run, `Merchant | Total Amount (AED) | # Transactions`,
   live `=SUMIF(...)` / `=COUNTIF(...)` formulas against the `Transactions`
   tab, plus a bold `TOTAL` row. Also excludes `Payments & Transfers` via
   the same `CATEGORIES_EXCLUDED_FROM_SUMMARY` set, for the same reason —
   otherwise a bank payment would show up as its own outsized "merchant."
4. **Mapping Reference** — a dump of the full `merchant_mapping` table as
   it stood at the time of this run (not filtered to only the merchants
   seen in this batch), so the output is self-contained for sanity-checking.

## Duplicate detection (`flag_duplicates`)

Before building the workbook, transactions are grouped by
`(transaction_date, description.strip().upper(), round(amount, 2))`. A row
is flagged `Possible Duplicate = "Yes"` only if that key appears more than
once **and** the occurrences span more than one source file — i.e. two
identical-looking line items from the *same* PDF are not flagged (that's
plausibly two real, separate charges), but the same line item appearing in
two different uploaded statements (overlapping statement periods) is.
Flagged rows are not dropped or deduplicated automatically — a human
decides via the flag.

## Formula recalculation

openpyxl writes formulas as strings but never evaluates them, so
`recalculate_with_libreoffice` (`app/excel_builder.py`) best-effort shells
out to `soffice --headless --convert-to xlsx` to produce cached formula
results before the file is offered for download. If `soffice` isn't on
`PATH`, this is skipped and only logged server-side (`logger.warning` in
`recalculate_with_libreoffice`) — not surfaced in the UI, since it's an
operator/host detail the end user can't act on and the formulas are still
correct and will compute normally the first time the file is opened in
Excel. `stats.recalculated` (`app/main.py`) still carries the outcome
through to the summary template in case a future need to display it
arises.

## Storage and lifecycle

Neither output file is ever written to the database or disk long-term.
Each is held as in-memory bytes in `main.py`'s `_pending_downloads` dict
(guarded by a `threading.Lock`), keyed by its own random
`secrets.token_urlsafe(24)` token — the workbook and (when present) the
review CSV get separate tokens and separate `/download/<token>` links, each
tagged with its own `mimetype` so `/download/<token>` can serve either type
correctly. The workbook still passes through a temp file transiently for
the LibreOffice recalculation step; the review CSV never touches disk.
Every entry is deleted either immediately after `/download/<token>` serves
it or after a 30-minute TTL sweep (`_sweep_expired_downloads`, run at the
top of `/upload` and `/download`) — whichever comes first.
