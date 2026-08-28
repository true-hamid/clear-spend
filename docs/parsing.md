# PDF parsing

**Implementation**: `app/pdf_parser.py`.

> **Validated against real statements from two banks**: Emirates NBD
> (`tests/fixtures/sample_statement.pdf`, a redacted anonymized export — see
> `tests/test_pdf_parser.py`'s `TestRealSampleStatement`) and a Mashreq
> Solitaire statement (used to derive the behaviors below and the synthetic
> tests in `TestSyntheticEdgeCases`, but not checked into the repo — real
> statements contain personal financial data). Row parsing is intentionally
> **bank-agnostic**: one shape-driven regex ("two dates, a description, a
> trailing amount") handles both, rather than a parsing path per bank. A
> statement from a third bank/layout may still need further tuning if it
> deviates from the variations already handled — see below.

## Behavior

- Text is extracted per page via `pdfplumber` (`page.extract_text()`); no
  OCR fallback is implemented (spec allows one, not built — add one to
  `parse_pdf` if a scanned/image-only statement shows up).
- Column-header lines are dropped wherever they appear, via `HEADER_LINE_RE`
  and `_strip_header_lines` — they never match the transaction-row shape
  below anyway, so this is mostly a no-op safety net. This used to instead
  truncate a page to "only the lines after the *last* header," on the
  assumption that bilingual (Arabic + English) statements duplicate every
  transaction *row* once per language and the earlier copy needed
  discarding to avoid double-counting. Real statements don't do that — only
  the column *labels* repeat (once per language, or once per cardholder
  sub-table on a multi-cardholder statement like Mashreq's) — so truncating
  silently dropped every earlier section's transactions. `HEADER_LINE_RE`
  matches both the spelled-out "Transaction Date ... Posting Date" wording
  and the "Trans. Date ... Posting Date" abbreviation, case-insensitively,
  regardless of whether the two phrases are separated by other text (as
  happens when a header wraps or two languages interleave).
- Each remaining line is matched against `TXN_LINE_RE`: two date tokens, a
  description, and a trailing amount with an optional credit marker —
  `DD/MM[/YYYY] DD/MM[/YYYY] <description> <amount>[CR|DR|-]`. Lines that
  don't match (headers, totals, footnotes, points summaries, interest-rate
  tables, sub-total lines, "Customer name ..." section separators) are
  silently skipped — none of them are shaped like two dates followed by a
  trailing amount. `SKIP_LINE_HINTS` is a defensive list of phrases that
  get skipped even if they happened to match the row pattern.
  - **Year-less dates**: some statements (Mashreq) print `DD/MM` with no
    year at all. `_infer_statement_year_month` anchors on the statement's
    own `"Statement date DD/MM/YYYY"` line; `_resolve_date` fills in the
    year, assuming the same year as the statement unless the transaction's
    month is *after* the statement's own month (e.g. a December transaction
    on a January statement), in which case it's the prior year — statements
    only ever look backward from their own date. A year-less date with no
    anchor available in the document is skipped rather than guessed.
  - **Credit markers**: a trailing `CR` (Emirates NBD) or a trailing ` -`
    (Mashreq, used for both refunds/credits and the payment line) both mark
    `is_credit=True`; `DR` is accepted as an explicit (no-op) debit marker.
    Matching is case-insensitive and the marker doesn't need a space before
    it (`14.56CR` and `14.56 CR` both match). The workbook builder
    (`app/excel_builder.py`) turns a credit into a negative signed amount
    and `Type = Credit`.
  - **Transaction reference numbers**: some statements (Mashreq) print a
    long reference number between the description and the amount, e.g.
    `sharaf d gdubai 74548995099023163736902 5,879.02`. `TXN_LINE_RE`'s
    non-greedy description group swallows it along with the merchant text;
    `TRAILING_REFERENCE_RE` strips it back off — any run of 8+ digits
    immediately before the amount is treated as a reference number, not
    part of the merchant name (a legitimate short numeric token in a
    merchant name, e.g. a store number, is always shorter and isn't
    necessarily the very last token before the amount, so it survives).
- A continuation line shaped like `(1 AED = USD 0.26392)` is appended to
  the *previous* transaction's description rather than treated as its own
  row (`FX_LINE_RE`). Some statements instead show the foreign-currency
  amount **inline** on the same line as the transaction (e.g.
  `*ANTHROPIC* CLAUDE SUB ANTHROPIC.CO 21.00 USD 79.57`) — `TXN_LINE_RE`'s
  non-greedy description group naturally backtracks to the *last* decimal
  number on the line as the AED amount, so this case needs no special
  handling; the foreign amount just stays embedded in the description
  (which is why this specific row goes through the fuzzy/keyword match
  path in `app/matching.py` rather than an exact match — its description
  never exactly equals the seed mapping's `original_description`).
- **Installment Plan section**: some statements append a separate
  "Installment Plan" sub-table below the main transaction list, listing EMI
  charges against a single `Date` column (no separate posting date), e.g.
  `26/04/2026 INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY 133.00
  Remaining Principal Balance 33.25`. `TXN_LINE_RE` can't match these (it
  requires two leading dates), so `INSTALLMENT_START_RE` handles them
  separately.
  - pdfplumber renders this row **inconsistently** — observed shapes, all
    handled by `parse_pdf`:
    1. One line, with the trailing "Remaining Balance" digits garbled by
       overlapping text (e.g. `Remaining Principal Balance3 333.2.255`) when
       two amount columns sit close together on the page.
    2. Split across several lines: the `EMI (cur/tot)` label alone, then
       `MERCHANT TOTAL_AMOUNT` on the next line, then the balance line, then
       a lone amount line.
    3. The real per-cycle amount lands right after the `EMI (cur/tot)`
       label, on the *same* line as the date (e.g.
       `26/04/2026 INSTALLMENT PLAN EMI (03/04) 33.25`), with
       `MERCHANT TOTAL_AMOUNT` on the next line and a redundant "Remaining
       ... Balance" line after that.
    `parse_pdf` matches the date + `EMI (cur/tot)` label first. If the text
    immediately after the label on that line is itself a bare amount (shape
    3), that's taken as the authoritative charged amount and the merchant
    name is read from the next line. Otherwise it looks for `MERCHANT
    AMOUNT` as trailing text on the same line (shape 1) or on the next
    non-blank line (shape 2).
  - `133.00` here is the row's **original full purchase price**, not what's
    actually billed this statement cycle — that's a smaller EMI amount
    (`33.25` above, i.e. `133.00 / 4` installments). `parse_pdf` looks a few
    lines ahead for a clean standalone `33.25`-shaped line (the real
    `Amount` column value) and uses that when found; if extraction garbled
    or dropped it, it falls back to `total_amount / installment_count`
    (from the `(cur/tot)` label) instead of the total price. Using the total
    price here was a bug — see `tests/test_pdf_parser.py`'s
    `test_debit_sum_matches_statement_purchase_total`, which cross-checks
    the sum of all parsed debit amounts against the statement's own
    "Purchase/Cash Advance (AED)" total.
  - `transaction_date` and `posting_date` are both set to the single date
    given. The reconstructed description
    (`"INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY"`) is built to
    exactly match the seed mapping row for this case — see
    `merchant_mapping.csv`.
  - Any Installment Plan line that doesn't fit either shape (no extractable
    `MERCHANT AMOUNT` pair) is logged at `WARNING` and skipped rather than
    silently dropped, so a further format drift shows up in the logs.
- **Deal Summary section (Mashreq)**: some banks track active
  installment/EMI plans in their own ledger table instead of a dated line
  item in the main transaction table, with **no merchant name at all**, e.g.:
  ```
  Type Date       Percentage  Amount  past due  Tenure  Instalment amount  Expiry date
  EPP  05/04/25   1,200.00    0.00    -400.00   1  3    -400.00            05/06/25
  ```
  `DEAL_ROW_RE` matches this shape generically (a leading word-token "type",
  then a date, then a fixed run of numeric columns) rather than hardcoding
  `EPP` specifically. The 8th column ("Instalment amount") is the amount
  actually charged this cycle — confirmed by summing all deal rows on the
  real sample statement and matching it exactly against that statement's
  own `"Total deal instalments"` summary figure (`3,704.95` — see
  `tests/test_pdf_parser.py`'s `TestDealSummary`). `transaction_date` and
  `posting_date` are both set to the deal's booking date (2-digit year,
  expanded via `_resolve_date`).
  - Since there's no merchant text to run through `MatchingEngine` at all,
    these rows set `RawTransaction.category_override = "Installments & EMI"`
    directly; `app/main.py:upload` honors that by skipping matching
    entirely for the row (treated as `matched=True`, so it never lands in
    Needs Review) and using the override as its category. The description
    is synthesized as `"{TYPE} DEAL INSTALMENT ({current tenure}/{total
    tenure})"`, e.g. `"EPP DEAL INSTALMENT (1/3)"`.
  - The other numeric columns (deal amount, percentage, outstanding amount,
    expiry date) aren't currently surfaced anywhere — only the booking date
    and the per-cycle instalment amount are used.
- Each `RawTransaction` carries `source_file` (the uploaded filename) and
  `statement_period` (best-effort extraction from the full document text
  via `_infer_statement_period`). It first tries a free-form
  `"Statement Period: 21-Jul-26 to 20-Aug-26"`-style range, then falls
  back to a `"Statement Date: DD/MM/YYYY"`-style single date; may be empty
  if neither pattern is present.
- Uploaded PDFs and parsed rows are **never written to the database** —
  they exist only for the duration of the `/upload` request
  (`app/main.py:upload`), written to a temp file, parsed, then deleted in
  a `finally` block.
