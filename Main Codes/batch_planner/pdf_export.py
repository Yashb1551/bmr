"""Render the on-screen BMR / ECR operation tables to a PDF for download.

Deliberately plain: a title, a few metadata lines, then one bordered table
per section, carrying *every* column that's shown on screen — the Actual
Temperature column included. Used by the Scheduler page's per-batch
"Download BMR / ECR (PDF)" buttons.

The document is intentionally not a fully formatted regulatory BMR/ECR — it's
the same operation/cleaning grid the page shows, in a portable file.
"""
import re
from dataclasses import dataclass

import pandas as pd
from fpdf import FPDF

# fpdf2's core fonts are Latin-1 only; real BMR text carries en-dashes, the
# degree sign, +/-, etc. Swap the common ones for ASCII, then drop anything
# still outside Latin-1 rather than raising.
_CHAR_SWAPS = {
    "–": "-", "—": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "°": " deg",
    "±": "+/-", "×": "x", "→": "->", " ": " ",
}


def _latin1(text) -> str:
    s = "" if text is None else str(text)
    for bad, good in _CHAR_SWAPS.items():
        s = s.replace(bad, good)
    return s.encode("latin-1", "replace").decode("latin-1")


def safe_filename(text: str) -> str:
    """A filesystem-safe slug for a batch label like 'UT/A028/07'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "batch"


@dataclass
class PdfSection:
    heading: str
    df: pd.DataFrame


def build_tables_pdf(title: str, meta_lines: list[str], sections: list[PdfSection]) -> bytes:
    pdf = FPDF(orientation="landscape", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 8, _latin1(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.set_font("Helvetica", "", 9)
    for line in meta_lines:
        pdf.multi_cell(0, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    for section in sections:
        if pdf.will_page_break(26):
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 6, _latin1(section.heading), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)

        df = section.df.fillna("")
        columns = [str(c) for c in df.columns]
        pdf.set_font("Helvetica", "", 8)
        with pdf.table(borders_layout="ALL", line_height=5, width=pdf.epw) as table:
            header = table.row()
            for col in columns:
                header.cell(_latin1(col))
            for _, record in df.iterrows():
                row = table.row()
                for col in columns:
                    row.cell(_latin1(record[col]))
        pdf.ln(4)

    return bytes(pdf.output())
