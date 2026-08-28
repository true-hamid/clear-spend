from pathlib import Path

from app.pdf_parser import _resolve_date, parse_pdf
from tests.conftest import SAMPLE_STATEMENT_PDF
from tests.pdf_helpers import build_pdf


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestRealSampleStatement:
    """Regression coverage against an actual (redacted) statement export —
    see docs/parsing.md for the format quirks this uncovered."""

    def test_parses_expected_transaction_count(self):
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        # 124 dated rows in the main Transactions table + 1 Installment
        # Plan EMI row.
        assert len(txns) == 125

    def test_infers_statement_period_from_free_form_range(self):
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        assert txns[0].statement_period == "21-Jul-26 to 20-Aug-26"

    def test_detects_all_credit_rows(self):
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        credits = [t for t in txns if t.is_credit]
        assert len(credits) == 3
        credit_amounts = sorted(t.amount for t in credits)
        assert credit_amounts == sorted([14.56, 302.45, 11165.90])

    def test_credit_sum_matches_statement_payments_credits_total(self):
        # Independent cross-check: the statement's own "Payments/Credits
        # (AED)" summary figure is 11,482.91.
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        total_credits = sum(t.amount for t in txns if t.is_credit)
        assert round(total_credits, 2) == 11482.91

    def test_captures_installment_plan_row(self):
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        installment = [t for t in txns if "INSTALLMENT PLAN EMI" in t.description]
        assert len(installment) == 1
        row = installment[0]
        assert row.description == "INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY"
        # 133.00 is the *original* purchase price embedded in the row text,
        # not what's actually billed this cycle. The statement's own
        # Installment Plan section lists a "Remaining Principal/Principle
        # Balance" of 33.25 for this row, and pdfplumber garbles those
        # trailing digits on this particular fixture — so the amount is
        # derived as 133.00 / 4 installments = 33.25 instead. See
        # test_credit_sum_matches... below and docs/parsing.md for the
        # cross-check against the statement's own Purchase/Cash Advance total.
        assert row.amount == 33.25
        assert row.is_credit is False
        assert row.transaction_date == "26/04/2026"

    def test_debit_sum_matches_statement_purchase_total(self):
        # Independent cross-check: the statement's own "Purchase/Cash
        # Advance (AED)" summary figure is 13,031.27. This only holds if the
        # Installment Plan row contributes its actual per-cycle charge
        # (33.25) rather than the original full purchase price (133.00).
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        total_debits = sum(t.amount for t in txns if not t.is_credit)
        assert round(total_debits, 2) == 13031.27

    def test_does_not_leak_non_transaction_rows(self):
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        joined = " ".join(t.description for t in txns)
        for leaked in ("Rewards Points", "Statement Summary", "Credit Limit", "Minimum Payment"):
            assert leaked not in joined

    def test_inline_foreign_currency_amount_kept_in_description(self):
        # No separate "(1 AED = ...)" continuation line in this statement —
        # the converted amount is inline, e.g. "... 21.00 USD 79.57". The
        # parser must pick the trailing 79.57 as the AED amount, not 21.00.
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        anthropic = [t for t in txns if "ANTHROPIC" in t.description]
        assert len(anthropic) == 1
        assert anthropic[0].amount == 79.57

    def test_handles_amount_with_no_space_before_cr(self):
        txns = parse_pdf(str(SAMPLE_STATEMENT_PDF), "sample_statement.pdf")
        talabat = [t for t in txns if "TALABAT" in t.description]
        assert len(talabat) == 1
        assert talabat[0].amount == 14.56
        assert talabat[0].is_credit is True


class TestSyntheticEdgeCases:
    def test_repeated_header_mid_page_does_not_drop_earlier_section(self, tmp_path):
        # A header line repeated mid-page (e.g. once per cardholder
        # sub-table on a multi-cardholder statement) must not cause earlier
        # sections' transactions to be discarded — only the header lines
        # themselves are dropped, everything else is kept.
        path = tmp_path / "bilingual.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date  Posting Date  Description  Amount",
                "01/07/2026 02/07/2026 FIRST SECTION MERCHANT 999.00",
                "Transaction Date  Posting Date  Description  Amount",
                "01/07/2026 02/07/2026 REAL MERCHANT ONE 50.00",
            ],
        )
        txns = parse_pdf(str(path), "bilingual.pdf")
        assert len(txns) == 2
        assert txns[0].description == "FIRST SECTION MERCHANT"
        assert txns[0].amount == 999.00
        assert txns[1].description == "REAL MERCHANT ONE"
        assert txns[1].amount == 50.00

    def test_trans_dot_date_header_abbreviation_is_recognized(self, tmp_path):
        path = tmp_path / "abbrev_header.pdf"
        build_pdf(
            str(path),
            [
                "Trans. Date Posting Date Description Amount (AED)",
                "01/07/2026 02/07/2026 SOME MERCHANT 50.00",
            ],
        )
        txns = parse_pdf(str(path), "abbrev_header.pdf")
        assert len(txns) == 1
        assert txns[0].description == "SOME MERCHANT"

    def test_fx_continuation_line_merges_into_previous_row(self, tmp_path):
        path = tmp_path / "fx.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "01/07/2026 02/07/2026 FOREIGN MERCHANT 45.00",
                "(1 AED = USD 0.27235)",
            ],
        )
        txns = parse_pdf(str(path), "fx.pdf")
        assert len(txns) == 1
        assert txns[0].description == "FOREIGN MERCHANT (1 AED = USD 0.27235)"
        assert txns[0].amount == 45.00

    def test_skip_line_hints_exclude_rows_shaped_like_transactions(self, tmp_path):
        # A line that *would* otherwise match TXN_LINE_RE (two dates + a
        # trailing amount) but is actually an interest-rate illustration
        # row must still be dropped via SKIP_LINE_HINTS.
        path = tmp_path / "summary.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "01/07/2026 02/07/2026 REAL MERCHANT 10.00",
                "01/07/2026 02/07/2026 Interest Rate Illustration 100.00",
            ],
        )
        txns = parse_pdf(str(path), "summary.pdf")
        assert len(txns) == 1
        assert txns[0].description == "REAL MERCHANT"

    def test_empty_pdf_yields_no_transactions(self, tmp_path):
        path = tmp_path / "empty.pdf"
        build_pdf(str(path), ["Nothing to see here."])
        txns = parse_pdf(str(path), "empty.pdf")
        assert txns == []

    def test_installment_plan_row_split_across_multiple_lines(self, tmp_path):
        # Some statements/pages render the Installment Plan sub-table as
        # several separate lines rather than one dash-delimited line (the
        # label alone, then "MERCHANT AMOUNT", then a "Remaining ... Balance"
        # line, then the real per-cycle amount on its own line).
        path = tmp_path / "installment_multiline.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "01/07/2026 02/07/2026 SOME MERCHANT 50.00",
                "26/04/2026 INSTALLMENT PLAN EMI (03/04)",
                "NUJOOM AL WARQA LAUNDRY 133.00",
                "Remaining Principle Balance 33.25",
                "33.25",
            ],
        )
        txns = parse_pdf(str(path), "installment_multiline.pdf")
        installment = [t for t in txns if "INSTALLMENT PLAN EMI" in t.description]
        assert len(installment) == 1
        row = installment[0]
        assert row.description == "INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY"
        assert row.amount == 33.25
        assert row.transaction_date == "26/04/2026"
        assert row.posting_date == "26/04/2026"
        # The normal transaction row before it must still parse untouched.
        assert any(t.description == "SOME MERCHANT" and t.amount == 50.00 for t in txns)

    def test_installment_plan_row_with_amount_inline_after_label(self, tmp_path):
        # pdfplumber sometimes places the row's real per-cycle Amount column
        # value right after the "EMI (cur/tot)" label on the same line as
        # the date, with the merchant name and original total price on the
        # following line — a third shape distinct from the single garbled
        # line and the fully-split multi-line case above.
        path = tmp_path / "installment_amount_inline.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "26/04/2026 INSTALLMENT PLAN EMI (03/04) 33.25",
                "NUJOOM AL WARQA LAUNDRY 133.00",
                "Remaining Principle Balance 33.25",
            ],
        )
        txns = parse_pdf(str(path), "installment_amount_inline.pdf")
        assert len(txns) == 1
        row = txns[0]
        assert row.description == "INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY"
        assert row.amount == 33.25
        assert row.transaction_date == "26/04/2026"

    def test_installment_plan_row_falls_back_to_division_when_amount_unavailable(self, tmp_path):
        # If no clean trailing amount line can be found at all, derive the
        # per-cycle charge from the total purchase amount and installment
        # count rather than silently dropping the row.
        path = tmp_path / "installment_no_trailing_amount.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "26/04/2026 INSTALLMENT PLAN EMI (03/04) NUJOOM AL WARQA LAUNDRY 133.00",
            ],
        )
        txns = parse_pdf(str(path), "installment_no_trailing_amount.pdf")
        assert len(txns) == 1
        assert txns[0].amount == 33.25

    def test_source_file_and_statement_period_are_recorded(self, tmp_path):
        path = tmp_path / "period.pdf"
        build_pdf(
            str(path),
            [
                "Statement Date: 20/08/2026",
                "Transaction Date Posting Date Description Amount",
                "01/07/2026 02/07/2026 SOME MERCHANT 10.00",
            ],
        )
        txns = parse_pdf(str(path), "period.pdf")
        assert txns[0].source_file == "period.pdf"
        assert txns[0].statement_period == "20/08/2026"

    def test_year_less_dates_resolved_from_statement_date(self, tmp_path):
        # Some statements (e.g. Mashreq) print transaction rows as "DD/MM"
        # with no year at all. The year is inferred from the statement's own
        # "Statement date DD/MM/YYYY" line: a transaction month at or before
        # the statement's month falls in the same year.
        path = tmp_path / "year_less.pdf"
        build_pdf(
            str(path),
            [
                "Statement date 08/05/2025",
                "transaction date posting date description reference amount",
                "09/04 11/04 some merchant 74548995099023163736902 5,879.02",
            ],
        )
        txns = parse_pdf(str(path), "year_less.pdf")
        assert len(txns) == 1
        assert txns[0].transaction_date == "09/04/2025"
        assert txns[0].posting_date == "11/04/2025"
        assert txns[0].description == "some merchant"
        assert txns[0].amount == 5879.02

    def test_year_less_date_rolls_back_a_year_past_statement_month(self, tmp_path):
        # A transaction dated after the statement's own month (e.g. December
        # on a January statement) must be assumed to be the *prior* year —
        # statements only ever look backward from their own date.
        path = tmp_path / "year_rollover.pdf"
        build_pdf(
            str(path),
            [
                "Statement date 10/01/2026",
                "transaction date posting date description amount",
                "15/12 16/12 year end purchase 40.00",
            ],
        )
        txns = parse_pdf(str(path), "year_rollover.pdf")
        assert len(txns) == 1
        assert txns[0].transaction_date == "15/12/2025"
        assert txns[0].posting_date == "16/12/2025"

    def test_year_less_date_without_statement_date_anchor_is_skipped(self, tmp_path):
        # With no "Statement date" line to anchor against, a year-less date
        # can't be safely resolved — the row is skipped rather than guessed.
        path = tmp_path / "no_anchor.pdf"
        build_pdf(
            str(path),
            [
                "transaction date posting date description amount",
                "09/04 11/04 some merchant 50.00",
            ],
        )
        txns = parse_pdf(str(path), "no_anchor.pdf")
        assert txns == []

    def test_trailing_hyphen_marks_a_credit_row(self, tmp_path):
        # Some statements mark a credit/refund with a trailing " -" instead
        # of "CR".
        path = tmp_path / "hyphen_credit.pdf"
        build_pdf(
            str(path),
            [
                "Statement date 08/05/2025",
                "transaction date posting date description amount",
                "11/04 11/04 mobile paymentdubai 20,068.35 -",
            ],
        )
        txns = parse_pdf(str(path), "hyphen_credit.pdf")
        assert len(txns) == 1
        assert txns[0].is_credit is True
        assert txns[0].amount == 20068.35
        assert txns[0].description == "mobile paymentdubai"

    def test_trailing_transaction_reference_number_is_stripped(self, tmp_path):
        # A long transaction-reference number between the description and
        # the amount (seen on Mashreq statements) must not end up glued onto
        # the merchant description.
        path = tmp_path / "reference.pdf"
        build_pdf(
            str(path),
            [
                "Statement date 08/05/2025",
                "transaction date posting date description reference amount",
                "10/04 11/04 urbanclapdubai 24119915100000199222957 93.00",
            ],
        )
        txns = parse_pdf(str(path), "reference.pdf")
        assert len(txns) == 1
        assert txns[0].description == "urbanclapdubai"
        assert txns[0].amount == 93.00

    def test_short_numeric_text_in_description_is_not_stripped_as_reference(self, tmp_path):
        # A short numeric token that's legitimately part of a merchant name
        # (a store/unit number) must survive — only long (8+ digit)
        # reference-shaped tokens immediately before the amount are stripped.
        path = tmp_path / "short_number.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "01/07/2026 02/07/2026 ADNOC GHOROOB 378 110.00",
            ],
        )
        txns = parse_pdf(str(path), "short_number.pdf")
        assert len(txns) == 1
        assert txns[0].description == "ADNOC GHOROOB 378"


class TestDealSummary:
    """Mashreq-style "Deal Summary" table: active installment/EMI plans with
    no merchant detail at all, listed separately from the main transaction
    table. See docs/parsing.md."""

    def test_deal_row_becomes_a_categorized_transaction(self, tmp_path):
        path = tmp_path / "deal_summary.pdf"
        build_pdf(
            str(path),
            [
                "Deal Summary",
                "Type Date Percentage Amount past due Tenure Instalment amount Expiry date",
                "EPP 05/04/25 1,200.00 0.00 -400.00 1 3 -400.00 05/06/25",
            ],
        )
        txns = parse_pdf(str(path), "deal_summary.pdf")
        assert len(txns) == 1
        row = txns[0]
        assert row.description == "EPP DEAL INSTALMENT (1/3)"
        assert row.amount == 400.00
        assert row.is_credit is False
        assert row.transaction_date == "05/04/2025"
        assert row.posting_date == "05/04/2025"
        assert row.category_override == "Installments & EMI"

    def test_multiple_deal_rows_all_captured(self, tmp_path):
        # Cross-check against the real statement: three deals summing to
        # exactly the statement's own "Total deal instalments 3,704.95".
        path = tmp_path / "deal_summary_multi.pdf"
        build_pdf(
            str(path),
            [
                "Deal Summary",
                "Type Date Percentage Amount past due Tenure Instalment amount Expiry date",
                "EPP 05/04/25 1,200.00 0.00 -400.00 1 3 -400.00 05/06/25",
                "EPP 05/04/25 1,211.00 0.00 -403.71 1 3 -403.70 05/06/25",
                "EPP 05/04/25 8,704.00 0.00 -2,901.25 1 3 -2,901.25 05/06/25",
            ],
        )
        txns = parse_pdf(str(path), "deal_summary_multi.pdf")
        assert len(txns) == 3
        assert round(sum(t.amount for t in txns), 2) == 3704.95

    def test_deal_row_does_not_collide_with_normal_transaction_row(self, tmp_path):
        path = tmp_path / "deal_and_txn.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "01/07/2026 02/07/2026 SOME MERCHANT 50.00",
                "Deal Summary",
                "Type Date Percentage Amount past due Tenure Instalment amount Expiry date",
                "EPP 05/04/25 1,200.00 0.00 -400.00 1 3 -400.00 05/06/25",
            ],
        )
        txns = parse_pdf(str(path), "deal_and_txn.pdf")
        assert len(txns) == 2
        assert any(t.description == "SOME MERCHANT" and t.category_override is None for t in txns)
        assert any(t.category_override == "Installments & EMI" for t in txns)

    def test_deal_row_with_invalid_calendar_date_is_skipped(self, tmp_path):
        # Digit-shaped but not a real date (month 13, day 32) — matches
        # DEAL_ROW_RE's shape but must fail _resolve_date's validity check.
        path = tmp_path / "deal_bad_date.pdf"
        build_pdf(
            str(path),
            [
                "Deal Summary",
                "Type Date Percentage Amount past due Tenure Instalment amount Expiry date",
                "EPP 32/13/25 1,200.00 0.00 -400.00 1 3 -400.00 05/06/25",
            ],
        )
        txns = parse_pdf(str(path), "deal_bad_date.pdf")
        assert txns == []


class TestResolveDate:
    def test_invalid_calendar_date_returns_none(self):
        assert _resolve_date("31/02/2025", None) is None


class TestPageAndLineSkipping:
    """These two skip branches (an entirely textless page, and a blank line
    within an otherwise real page) are impractical to force out of fpdf's
    PDF renderer + pdfplumber's text extraction reliably, so they're
    exercised against a minimal fake standing in for pdfplumber's own
    open()/pages/extract_text() surface instead of a real PDF file."""

    def test_page_with_no_extractable_text_is_skipped(self, monkeypatch):
        fake_pdf = _FakePdf([_FakePage(None)])
        monkeypatch.setattr("app.pdf_parser.pdfplumber.open", lambda path: fake_pdf)
        txns = parse_pdf("ignored.pdf", "fake.pdf")
        assert txns == []

    def test_blank_line_within_page_text_is_skipped(self, monkeypatch):
        text = (
            "Transaction Date Posting Date Description Amount\n"
            "\n"
            "01/07/2026 02/07/2026 SOME MERCHANT 50.00"
        )
        fake_pdf = _FakePdf([_FakePage(text)])
        monkeypatch.setattr("app.pdf_parser.pdfplumber.open", lambda path: fake_pdf)
        txns = parse_pdf("ignored.pdf", "fake.pdf")
        assert len(txns) == 1
        assert txns[0].description == "SOME MERCHANT"


class TestInstallmentPlanMalformedRows:
    def test_invalid_calendar_date_is_skipped(self, tmp_path):
        path = tmp_path / "bad_installment_date.pdf"
        build_pdf(
            str(path),
            [
                "Transaction Date Posting Date Description Amount",
                "31/02/2026 INSTALLMENT PLAN EMI (01/04) SOME MERCHANT 100.00",
            ],
        )
        txns = parse_pdf(str(path), "bad_installment_date.pdf")
        assert txns == []

    def test_unparseable_merchant_line_falls_back_to_nearby_bare_amount(self, tmp_path):
        # The merchant line has no trailing amount the parser can pull off
        # it, but a clean standalone number on the very next line is still
        # picked up as the authoritative per-cycle charge.
        path = tmp_path / "installment_text_only_merchant.pdf"
        build_pdf(
            str(path),
            [
                "26/04/2026 INSTALLMENT PLAN EMI (01/04) MERCHANT WITH NO TRAILING AMOUNT",
                "33.25",
            ],
        )
        txns = parse_pdf(str(path), "installment_text_only_merchant.pdf")
        assert len(txns) == 1
        assert txns[0].amount == 33.25
        assert "MERCHANT WITH NO TRAILING AMOUNT" in txns[0].description

    def test_no_merchant_or_amount_at_all_is_skipped(self, tmp_path):
        # Nothing at all follows the "EMI (n/m)" label, and it's the last
        # line on the page — no next line to fall back to either.
        path = tmp_path / "installment_no_merchant.pdf"
        build_pdf(str(path), ["26/04/2026 INSTALLMENT PLAN EMI (01/04)"])
        txns = parse_pdf(str(path), "installment_no_merchant.pdf")
        assert txns == []

    def test_unparseable_merchant_line_with_no_nearby_amount_is_skipped(self, tmp_path):
        # Merchant text is present but has no parseable trailing amount, and
        # no bare number shows up in the following lines either — there's
        # nothing usable at all, so the row is dropped.
        path = tmp_path / "installment_no_amount_anywhere.pdf"
        build_pdf(
            str(path),
            ["26/04/2026 INSTALLMENT PLAN EMI (01/04) MERCHANT WITH NO AMOUNT ANYWHERE NEARBY"],
        )
        txns = parse_pdf(str(path), "installment_no_amount_anywhere.pdf")
        assert txns == []
