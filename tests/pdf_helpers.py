"""Builds throwaway single-page statement PDFs for parser edge-case tests.
Not used at app runtime — test-only, hence fpdf2 lives in requirements-dev.txt
rather than requirements.txt."""
from fpdf import FPDF


def build_pdf(path: str, lines: list[str]) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=10)
    for line in lines:
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)
