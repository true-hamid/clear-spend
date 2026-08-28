"""Extracts transaction rows from credit card statement PDFs.

Row parsing is bank-agnostic by design rather than a set of per-bank code
paths: a transaction line is "two dates, a description, and a trailing
amount" regardless of which bank issued the statement, so one shape-driven
regex handles them all, tolerating the specific variations seen so far
(year present or absent on the dates, an embedded transaction-reference
number, several different credit-marker styles). See docs/parsing.md for
the details and the statements this has actually been validated against.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

logger = logging.getLogger(__name__)

# Two dates (year optional — some statements print DD/MM with no year at
# all), a description, and a trailing amount with an optional credit
# marker: a plain "CR" suffix, a "DR" suffix (explicit debit, a no-op), or a
# trailing " -" (seen on statements that mark credits/refunds this way
# instead of "CR"). Case-insensitive so "cr"/"Cr" also match.
TXN_LINE_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}(?:/\d{4})?)\s+(\d{1,2}/\d{1,2}(?:/\d{4})?)\s+(.+?)\s+([\d,]+\.\d{2})\s*(CR|DR|-)?$",
    re.IGNORECASE,
)
# Some statements print a long transaction-reference number between the
# description and the amount (e.g. "sharaf d gdubai 74548995099023163736902
# 5,879.02"). TXN_LINE_RE's non-greedy description group swallows it along
# with the merchant text; this strips it back off. 8+ digits distinguishes a
# reference number from any short numeric text that's legitimately part of a
# merchant name (store/unit numbers etc. are always shorter and, in
# practice, never sit immediately before the amount).
TRAILING_REFERENCE_RE = re.compile(r"^(.*\S)\s+(\d{8,})$")
CREDIT_MARKERS = {"CR", "-"}
FX_LINE_RE = re.compile(r"^\(1\s*AED\s*=.*\)$", re.IGNORECASE)
# "Trans(action)? Date" covers both the spelled-out header some statements use
# and the "Trans. Date" abbreviation seen on real Emirates NBD-style exports.
HEADER_LINE_RE = re.compile(
    r"trans(?:action)?\.?\s*date.*posting\s*date|posting\s*date.*trans(?:action)?\.?\s*date",
    re.IGNORECASE,
)
# The Installment Plan sub-table uses a single date column (not a
# transaction/posting date pair): "DD/MM/YYYY INSTALLMENT PLAN EMI (cur/tot)"
# followed by the merchant name and the *original* full purchase amount, then
# a "Remaining Principal/Principle Balance" figure. pdfplumber renders this
# row inconsistently across statements/pages — sometimes as one line with the
# trailing balance digits garbled by overlapping text, sometimes as several
# separate lines (label, then "MERCHANT AMOUNT", then the balance line, then
# a lone amount line). Both shapes are handled below; see docs/parsing.md.
INSTALLMENT_START_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+INSTAL{1,2}MENT PLAN EMI\s*\((\d+)\s*/\s*(\d+)\)\s*[—\-]?\s*(.*)$",
    re.IGNORECASE,
)
INSTALLMENT_MERCHANT_AMOUNT_RE = re.compile(r"^(.+?)\s+([\d,]+\.\d{2})\b")
BARE_AMOUNT_RE = re.compile(r"^([\d,]+\.\d{2})$")
INSTALLMENT_LOOKAHEAD_LINES = 3

# Some banks (Mashreq) list active installment/EMI plans in their own "Deal
# Summary" ledger rather than as a dated line item in the main transaction
# table, e.g.:
#   "EPP 05/04/25 1,200.00 0.00 -400.00 1 3 -400.00 05/06/25"
# columns: Type, Booking Date, Deal Amount, Percentage, Outstanding Amount,
# current tenure, total tenure, Instalment amount (this cycle's charge,
# confirmed against the statement's own "Total deal instalments" summary
# figure — see docs/parsing.md), Expiry Date. There's no merchant name at
# all in this table, so these rows get a fixed category rather than going
# through MatchingEngine (see RawTransaction.category_override).
DEAL_ROW_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9]*)\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+"
    r"-?([\d,]+\.\d{2})\s+(\d+)\s+(\d+)\s+-?([\d,]+\.\d{2})\s+(\d{1,2}/\d{1,2}/\d{2,4})$"
)
DEAL_INSTALLMENT_CATEGORY = "Installments & EMI"

# Content that should never be mistaken for a transaction row even if it
# happens to contain two dates and a number (defensive skip-list).
SKIP_LINE_HINTS = (
    "interest rate",
    "minimum payment",
    "total dues",
    "statement summary",
    "reward points",
    "available credit limit",
)


@dataclass
class RawTransaction:
    transaction_date: str
    posting_date: str
    description: str
    amount: float
    is_credit: bool
    source_file: str
    statement_period: str = ""
    # Set for rows with no merchant detail to match against (e.g. a Deal
    # Summary/EMI ledger row) — bypasses MatchingEngine entirely and assigns
    # this category directly, rather than landing in Needs Review for having
    # nothing a human could usefully map.
    category_override: str | None = None


def _strip_header_lines(page_text: str) -> list[str]:
    """Drop column-header lines rather than truncating everything before the
    last one on the page. An earlier version kept only the text after the
    last header, on the assumption that bilingual statements duplicate each
    transaction *row* once per language and the earlier copy needed to be
    discarded to avoid double-counting. Real statements don't do that — only
    the column *labels* repeat, e.g. once per cardholder sub-table on a
    multi-cardholder Mashreq statement — so truncating silently dropped
    every earlier section's transactions. Header lines never match the
    transaction-row shape below anyway, so simply dropping them is enough."""
    return [line for line in page_text.splitlines() if not HEADER_LINE_RE.search(line)]


def _infer_statement_period(all_text: str) -> str:
    # "Statement Period: 21-Jul-26 to 20-Aug-26" (real-world format) — a
    # free-form date range, not necessarily DD/MM/YYYY.
    match = re.search(
        r"statement\s*period\s*[:\-]?\s*([0-9A-Za-z]{1,9}[-/][A-Za-z0-9]{1,9}[-/][0-9]{2,4}\s*to\s*[0-9A-Za-z]{1,9}[-/][A-Za-z0-9]{1,9}[-/][0-9]{2,4})",
        all_text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    match = re.search(r"statement\s*date\s*[:\-]?\s*([0-9/]{8,10})", all_text, re.IGNORECASE)
    return match.group(1) if match else ""


def _infer_statement_year_month(all_text: str) -> tuple[int, int] | None:
    """Anchor for expanding transaction dates that omit a year entirely
    (e.g. Mashreq's "DD/MM" rows), drawn from the statement's own
    "Statement date DD/MM/YYYY" line. A transaction dated at or before the
    statement's month is assumed to fall in the same year; a later month
    (e.g. a December transaction on a January statement) is assumed to be
    the prior year — statements only ever look backward from their own
    date."""
    match = re.search(r"statement\s*date\s*[:\-]?\s*\d{1,2}/(\d{1,2})/(\d{4})", all_text, re.IGNORECASE)
    if not match:
        return None
    month, year = match.groups()
    return int(year), int(month)


def _resolve_date(date_str: str, anchor: tuple[int, int] | None) -> str | None:
    """Validate a DD/MM, DD/MM/YY, or DD/MM/YYYY token and normalize it to
    DD/MM/YYYY. A 2-digit year is expanded as 2000+YY. A missing year is
    filled in from `anchor` (see `_infer_statement_year_month`); returns
    None if the date is invalid or a year is missing with no anchor
    available."""
    day_str, month_str, *rest = date_str.split("/")
    if rest:
        year = int(rest[0]) + 2000 if len(rest[0]) == 2 else int(rest[0])
    elif anchor:
        anchor_year, anchor_month = anchor
        year = anchor_year if int(month_str) <= anchor_month else anchor_year - 1
    else:
        return None
    try:
        return datetime(year, int(month_str), int(day_str)).strftime("%d/%m/%Y")
    except ValueError:
        return None


def parse_pdf(file_path: str, source_file: str) -> list[RawTransaction]:
    transactions: list[RawTransaction] = []
    with pdfplumber.open(file_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        statement_period = _infer_statement_period(full_text)
        statement_year_month = _infer_statement_year_month(full_text)

        for page in pdf.pages:
            text = page.extract_text() or ""
            if not text:
                continue
            block_lines = _strip_header_lines(text)
            i = 0
            while i < len(block_lines):
                line = block_lines[i].strip()
                i += 1
                if not line:
                    continue

                if FX_LINE_RE.match(line) and transactions:
                    transactions[-1].description = f"{transactions[-1].description} {line}".strip()
                    continue

                if any(hint in line.lower() for hint in SKIP_LINE_HINTS):
                    continue

                installment_start = INSTALLMENT_START_RE.match(line)
                if installment_start:
                    inst_date, current_n, total_n, remainder = installment_start.groups()
                    try:
                        datetime.strptime(inst_date, "%d/%m/%Y")
                    except ValueError:
                        continue

                    remainder = remainder.strip()

                    # pdfplumber sometimes places the row's real per-cycle
                    # "Amount" column value immediately after the EMI label,
                    # on the same line as the date — a bare number with no
                    # merchant text ahead of it. When that happens it's
                    # authoritative; the merchant name still comes from the
                    # next line ("MERCHANT TOTAL_AMOUNT").
                    charged_amount = None
                    bare_remainder = BARE_AMOUNT_RE.match(remainder)
                    if bare_remainder:
                        charged_amount = float(bare_remainder.group(1).replace(",", ""))
                        merchant_amount_text = ""
                    else:
                        merchant_amount_text = remainder

                    if not merchant_amount_text and i < len(block_lines):
                        merchant_amount_text = block_lines[i].strip()
                        i += 1

                    merchant_amount_match = INSTALLMENT_MERCHANT_AMOUNT_RE.match(merchant_amount_text)
                    total_amount = None
                    if merchant_amount_match:
                        merchant, total_amount_str = merchant_amount_match.groups()
                        total_amount = float(total_amount_str.replace(",", ""))
                    elif merchant_amount_text:
                        # No trailing amount we can parse on the merchant
                        # line; still usable as long as we already have an
                        # authoritative charged_amount from the same line
                        # as the label (see above).
                        logger.warning(
                            "Installment Plan merchant line has no parseable amount: %r", merchant_amount_text
                        )
                        merchant = merchant_amount_text
                    else:
                        logger.warning("Unrecognized Installment Plan row format, skipping: %r", line)
                        continue

                    # Otherwise prefer a clean standalone number nearby (the
                    # real Amount column value); fall back to deriving it
                    # from the total purchase amount and installment count
                    # (e.g. 133.00 / "04" installments) when that trailing
                    # text is garbled or missing entirely.
                    if charged_amount is None:
                        for offset in range(min(INSTALLMENT_LOOKAHEAD_LINES, len(block_lines) - i)):
                            bare = BARE_AMOUNT_RE.match(block_lines[i + offset].strip())
                            if bare:
                                charged_amount = float(bare.group(1).replace(",", ""))
                                break
                    if charged_amount is None:
                        if total_amount is None:
                            logger.warning("Installment Plan row has no usable amount, skipping: %r", line)
                            continue
                        installment_count = int(total_n)
                        charged_amount = (
                            round(total_amount / installment_count, 2) if installment_count else total_amount
                        )

                    label = f"INSTALLMENT PLAN EMI ({current_n}/{total_n})"
                    transactions.append(
                        RawTransaction(
                            transaction_date=inst_date,
                            posting_date=inst_date,
                            description=f"{label} {merchant.strip()}".strip(),
                            amount=charged_amount,
                            is_credit=False,
                            source_file=source_file,
                            statement_period=statement_period,
                        )
                    )
                    continue

                deal_match = DEAL_ROW_RE.match(line)
                if deal_match:
                    (
                        deal_type,
                        booking_date_raw,
                        _deal_amount,
                        _percentage,
                        _outstanding_amount,
                        current_tenure,
                        total_tenure,
                        instalment_amount_str,
                        _expiry_date,
                    ) = deal_match.groups()

                    booking_date = _resolve_date(booking_date_raw, statement_year_month)
                    if booking_date is None:
                        continue

                    transactions.append(
                        RawTransaction(
                            transaction_date=booking_date,
                            posting_date=booking_date,
                            description=f"{deal_type.upper()} DEAL INSTALMENT ({current_tenure}/{total_tenure})",
                            amount=float(instalment_amount_str.replace(",", "")),
                            is_credit=False,
                            source_file=source_file,
                            statement_period=statement_period,
                            category_override=DEAL_INSTALLMENT_CATEGORY,
                        )
                    )
                    continue

                match = TXN_LINE_RE.match(line)
                if not match:
                    continue

                txn_date_raw, post_date_raw, description, amount_str, marker = match.groups()

                reference_match = TRAILING_REFERENCE_RE.match(description)
                if reference_match:
                    description = reference_match.group(1)

                txn_date = _resolve_date(txn_date_raw, statement_year_month)
                post_date = _resolve_date(post_date_raw, statement_year_month)
                if txn_date is None or post_date is None:
                    continue

                amount = float(amount_str.replace(",", ""))
                transactions.append(
                    RawTransaction(
                        transaction_date=txn_date,
                        posting_date=post_date,
                        description=description.strip(),
                        amount=amount,
                        is_credit=marker is not None and marker.upper() in CREDIT_MARKERS,
                        source_file=source_file,
                        statement_period=statement_period,
                    )
                )
    return transactions
