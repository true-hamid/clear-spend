from pathlib import Path
from unittest.mock import MagicMock

from app.excel_builder import (
    ProcessedTransaction,
    build_review_csv,
    build_workbook,
    flag_duplicates,
    recalculate_with_libreoffice,
)
from app.matching import UNCATEGORIZED
from app.pdf_parser import RawTransaction


def _raw(desc, amount, is_credit=False, source_file="a.pdf", date="01/07/2026"):
    return RawTransaction(
        transaction_date=date,
        posting_date=date,
        description=desc,
        amount=amount,
        is_credit=is_credit,
        source_file=source_file,
        statement_period="",
    )


def _processed(raw, cleaned_name=None, category="Groceries", matched=True):
    return ProcessedTransaction(
        raw=raw,
        cleaned_name=cleaned_name or raw.description.title(),
        category=category,
        matched=matched,
    )


class TestFlagDuplicates:
    def test_same_key_across_two_files_is_flagged(self):
        p1 = _processed(_raw("CARREFOUR", 100.0, source_file="jan.pdf"))
        p2 = _processed(_raw("CARREFOUR", 100.0, source_file="feb.pdf"))
        flag_duplicates([p1, p2])
        assert p1.possible_duplicate is True
        assert p2.possible_duplicate is True

    def test_same_key_within_one_file_is_not_flagged(self):
        # Two genuinely separate same-day, same-amount charges at the same
        # merchant within a single statement are plausible, not a dupe.
        p1 = _processed(_raw("CARREFOUR", 100.0, source_file="jan.pdf"))
        p2 = _processed(_raw("CARREFOUR", 100.0, source_file="jan.pdf"))
        flag_duplicates([p1, p2])
        assert p1.possible_duplicate is False
        assert p2.possible_duplicate is False

    def test_different_amount_is_not_flagged(self):
        p1 = _processed(_raw("CARREFOUR", 100.0, source_file="jan.pdf"))
        p2 = _processed(_raw("CARREFOUR", 50.0, source_file="feb.pdf"))
        flag_duplicates([p1, p2])
        assert p1.possible_duplicate is False
        assert p2.possible_duplicate is False


class TestBuildWorkbook:
    def test_creates_all_tabs(self):
        wb = build_workbook([], [])
        assert wb.sheetnames == [
            "Transactions",
            "Category Summary",
            "Merchant Summary",
            "Mapping Reference",
        ]

    def test_credit_stored_as_negative_amount_with_credit_type(self):
        raw = _raw("PAYMENT RECEIVED", 200.0, is_credit=True)
        wb = build_workbook([_processed(raw, category="Payments & Transfers")], [])
        ws = wb["Transactions"]
        row = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
        # columns: Trans Date, Posting Date, Original Desc, Cleaned, Category, Type, Amount, Source, Dup
        assert row[5] == "Credit"
        assert row[6] == -200.0

    def test_debit_stored_as_positive_amount_with_debit_type(self):
        raw = _raw("SOME SHOP", 75.0, is_credit=False)
        wb = build_workbook([_processed(raw)], [])
        ws = wb["Transactions"]
        row = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
        assert row[5] == "Debit"
        assert row[6] == 75.0

    def test_source_file_column_strips_extension(self):
        raw = _raw("SOME SHOP", 75.0, source_file="jan_statement.pdf")
        wb = build_workbook([_processed(raw)], [])
        ws = wb["Transactions"]
        row = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
        assert row[7] == "jan_statement"

    def test_transactions_header_has_source_file_column_not_statement_period(self):
        wb = build_workbook([], [])
        ws = wb["Transactions"]
        headers = list(ws.iter_rows(min_row=1, max_row=1, values_only=True))[0]
        assert headers[7] == "Source File"

    def test_category_summary_has_live_formulas_not_precomputed_values(self):
        raw = _raw("SOME SHOP", 75.0)
        wb = build_workbook([_processed(raw, category="Groceries")], [])
        ws = wb["Category Summary"]
        row = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
        assert row[0] == "Groceries"
        assert isinstance(row[1], str) and row[1].startswith("=SUMIF(")
        assert isinstance(row[2], str) and row[2].startswith("=COUNTIF(")

    def test_category_summary_total_row_sums_all_categories(self):
        p1 = _processed(_raw("A", 10.0), category="Groceries")
        p2 = _processed(_raw("B", 20.0), category="Dining & Food Delivery")
        wb = build_workbook([p1, p2], [])
        ws = wb["Category Summary"]
        rows = list(ws.iter_rows(values_only=True))
        total_row = rows[-1]
        assert total_row[0] == "TOTAL"
        assert total_row[1].startswith("=SUM(")

    def test_category_summary_excludes_payments_and_transfers(self):
        # Payments toward a previous statement's balance aren't spending —
        # they shouldn't be rolled up (or counted toward TOTAL) in the
        # Category Summary tab, even though they still appear on
        # Transactions.
        spend = _processed(_raw("SOME SHOP", 75.0), category="Groceries")
        payment = _processed(
            _raw("TRANSFER PAYMENT RECEIVED THANK YOU", 200.0, is_credit=True),
            category="Payments & Transfers",
        )
        wb = build_workbook([spend, payment], [])

        ws = wb["Category Summary"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        categories = [row[0] for row in rows]
        assert "Payments & Transfers" not in categories
        assert "Groceries" in categories
        # Only one category row (Groceries) plus the TOTAL row.
        assert len(rows) == 2

        # Still present on the Transactions tab for the record.
        txn_ws = wb["Transactions"]
        txn_categories = [row[4] for row in txn_ws.iter_rows(min_row=2, values_only=True)]
        assert "Payments & Transfers" in txn_categories

    def test_merchant_summary_has_live_formulas_not_precomputed_values(self):
        raw = _raw("SOME SHOP", 75.0)
        wb = build_workbook([_processed(raw, cleaned_name="Some Shop")], [])
        ws = wb["Merchant Summary"]
        row = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
        assert row[0] == "Some Shop"
        assert isinstance(row[1], str) and row[1].startswith("=SUMIF(")
        assert isinstance(row[2], str) and row[2].startswith("=COUNTIF(")

    def test_merchant_summary_sums_same_merchant_across_rows(self):
        p1 = _processed(_raw("CARREFOUR #1", 10.0), cleaned_name="Carrefour")
        p2 = _processed(_raw("CARREFOUR #2", 20.0), cleaned_name="Carrefour")
        p3 = _processed(_raw("NOON", 5.0), cleaned_name="Noon")
        wb = build_workbook([p1, p2, p3], [])
        ws = wb["Merchant Summary"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        merchants = [row[0] for row in rows]
        assert merchants == ["Carrefour", "Noon", "TOTAL"]
        assert rows[-1][1].startswith("=SUM(")

    def test_merchant_summary_excludes_payments_and_transfers(self):
        spend = _processed(_raw("SOME SHOP", 75.0), cleaned_name="Some Shop", category="Groceries")
        payment = _processed(
            _raw("TRANSFER PAYMENT RECEIVED THANK YOU", 200.0, is_credit=True),
            cleaned_name="Payment Received",
            category="Payments & Transfers",
        )
        wb = build_workbook([spend, payment], [])

        ws = wb["Merchant Summary"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        merchants = [row[0] for row in rows]
        assert "Payment Received" not in merchants
        assert "Some Shop" in merchants
        assert len(rows) == 2  # Some Shop + TOTAL

        # Still present on the Transactions tab for the record.
        txn_ws = wb["Transactions"]
        txn_merchants = [row[3] for row in txn_ws.iter_rows(min_row=2, values_only=True)]
        assert "Payment Received" in txn_merchants

    def test_mapping_reference_dumps_all_supplied_rows(self):
        mapping_rows = [
            {"original_description": "X", "cleaned_name": "X Clean", "category": "Retail"},
            {"original_description": "Y", "cleaned_name": "Y Clean", "category": "Shopping"},
        ]
        wb = build_workbook([], mapping_rows)
        ws = wb["Mapping Reference"]
        assert ws.max_row == 3  # header + 2 rows

    def test_amount_column_uses_red_negative_number_format(self):
        raw = _raw("SOME SHOP", 75.0)
        wb = build_workbook([_processed(raw)], [])
        ws = wb["Transactions"]
        assert ws.cell(row=2, column=7).number_format == "#,##0.00;[RED](#,##0.00)"

    def test_header_row_is_frozen_and_autofiltered(self):
        raw = _raw("SOME SHOP", 75.0)
        wb = build_workbook([_processed(raw)], [])
        ws = wb["Transactions"]
        assert ws.freeze_panes == "A2"
        assert ws.auto_filter.ref is not None


class TestBuildReviewCsv:
    def test_returns_none_when_nothing_needs_review(self):
        matched = _processed(_raw("KNOWN SHOP", 10.0), category="Groceries", matched=True)
        assert build_review_csv([matched]) is None

    def test_returns_none_for_empty_input(self):
        assert build_review_csv([]) is None

    def test_only_contains_uncategorized_rows(self):
        matched = _processed(_raw("KNOWN SHOP", 10.0), category="Groceries", matched=True)
        unmatched = _processed(_raw("UNKNOWN SHOP", 20.0), category=UNCATEGORIZED, matched=False)
        csv_text = build_review_csv([matched, unmatched])
        lines = csv_text.strip().splitlines()
        assert lines == ["Original Description,Type", "UNKNOWN SHOP,Debit"]

    def test_credit_rows_are_labeled_credit(self):
        unmatched = _processed(
            _raw("UNKNOWN REFUND", 20.0, is_credit=True), category=UNCATEGORIZED, matched=False
        )
        csv_text = build_review_csv([unmatched])
        lines = csv_text.strip().splitlines()
        assert lines[1] == "UNKNOWN REFUND,Credit"

    def test_multiple_unrecognized_rows_all_included(self):
        u1 = _processed(_raw("UNKNOWN A", 5.0), category=UNCATEGORIZED, matched=False)
        u2 = _processed(_raw("UNKNOWN B", 15.0, is_credit=True), category=UNCATEGORIZED, matched=False)
        matched = _processed(_raw("KNOWN SHOP", 10.0), category="Groceries", matched=True)
        csv_text = build_review_csv([matched, u1, u2])
        lines = csv_text.strip().splitlines()
        assert lines == [
            "Original Description,Type",
            "UNKNOWN A,Debit",
            "UNKNOWN B,Credit",
        ]


class TestRecalculateWithLibreoffice:
    def test_returns_false_when_soffice_not_on_path(self, monkeypatch):
        monkeypatch.setattr("app.excel_builder.shutil.which", lambda name: None)
        assert recalculate_with_libreoffice("whatever.xlsx") is False

    def test_returns_true_and_copies_converted_file_over_original(self, monkeypatch, tmp_path):
        xlsx_path = tmp_path / "workbook.xlsx"
        xlsx_path.write_bytes(b"original")
        monkeypatch.setattr("app.excel_builder.shutil.which", lambda name: "/usr/bin/soffice")

        def fake_run(cmd, capture_output, timeout):
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            (out_dir / xlsx_path.name).write_bytes(b"recalculated")
            return MagicMock(returncode=0, stderr=b"")

        monkeypatch.setattr("app.excel_builder.subprocess.run", fake_run)

        assert recalculate_with_libreoffice(str(xlsx_path)) is True
        assert xlsx_path.read_bytes() == b"recalculated"

    def test_returns_false_on_nonzero_returncode(self, monkeypatch, tmp_path):
        xlsx_path = tmp_path / "workbook.xlsx"
        xlsx_path.write_bytes(b"original")
        monkeypatch.setattr("app.excel_builder.shutil.which", lambda name: "/usr/bin/soffice")
        monkeypatch.setattr(
            "app.excel_builder.subprocess.run",
            lambda *a, **k: MagicMock(returncode=1, stderr=b"boom"),
        )
        assert recalculate_with_libreoffice(str(xlsx_path)) is False

    def test_returns_false_when_converted_file_is_missing(self, monkeypatch, tmp_path):
        xlsx_path = tmp_path / "workbook.xlsx"
        xlsx_path.write_bytes(b"original")
        monkeypatch.setattr("app.excel_builder.shutil.which", lambda name: "/usr/bin/soffice")
        monkeypatch.setattr(
            "app.excel_builder.subprocess.run",
            lambda *a, **k: MagicMock(returncode=0, stderr=b""),
        )
        assert recalculate_with_libreoffice(str(xlsx_path)) is False

    def test_returns_false_on_unexpected_exception(self, monkeypatch, tmp_path):
        xlsx_path = tmp_path / "workbook.xlsx"
        xlsx_path.write_bytes(b"original")
        monkeypatch.setattr("app.excel_builder.shutil.which", lambda name: "/usr/bin/soffice")

        def boom(*a, **k):
            raise RuntimeError("subprocess exploded")

        monkeypatch.setattr("app.excel_builder.subprocess.run", boom)
        assert recalculate_with_libreoffice(str(xlsx_path)) is False
