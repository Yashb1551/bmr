"""Shared table-styling helpers for the on-screen BMR/ECR operation tables."""
import re

import pandas as pd

QC_SAMPLE_RE = re.compile(r"\bqc\b|sample", re.IGNORECASE)


def is_qc_sample(text: str) -> bool:
    return bool(QC_SAMPLE_RE.search(str(text)))


def highlight_qc_sample(df: pd.DataFrame, operation_col: str = "Operation"):
    """Returns a pandas Styler that colors an entire row green whenever its
    Operation text mentions QC or a sample — used for both the BMR operation
    table and the ECR cleaning-step table."""
    def _row_style(row):
        if is_qc_sample(row.get(operation_col, "")):
            return ["background-color: #c6efce; color: #006100"] * len(row)
        return [""] * len(row)
    return df.style.apply(_row_style, axis=1)


def style_by_value(df: pd.DataFrame, column: str, colors: dict[str, str]):
    """Returns a pandas Styler that colors an entire row based on a lookup
    of one column's value (e.g. a Status column) — used for the Equipment
    Cleaning Schedule dashboard."""
    def _row_style(row):
        css = colors.get(row.get(column), "")
        return [css] * len(row)
    return df.style.apply(_row_style, axis=1)
