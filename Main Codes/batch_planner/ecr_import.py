"""ECR (Equipment Cleaning Record) template import — reads the plant's
per-equipment-category cleaning procedure documents (Database/ECR/*.doc(x))
into an ordered list of cleaning steps, reusing the exact same table-finding
and duration-computation logic as bmr.py's BMR parsing (these documents use
the identical "Op. No. / Operation / Date / Initial / Final" table layout,
just with extra Done-by/Remarks columns we don't need).

Cleaning steps repeat their Op. No. across two consecutive rows whenever a
step needs a "Checked By:" signature — the second row is a duplicate of the
first (same Op. No., same Operation text) with the signature placeholder in
the time columns instead of real content, so it's dropped rather than
counted as its own step. A "Step No. <n>: <name>" section-header row (merged
across every column, like the BMR "Stage" headers) is skipped the same way.
"""
from dataclasses import dataclass, field
from pathlib import Path

from . import bmr, equipment_import


@dataclass
class ParsedECR:
    equipment_name: str
    category: str
    subtype: str | None
    steps: list[bmr.ParsedOperation] = field(default_factory=list)


def _equipment_name_from_header(doc) -> str:
    for section in doc.sections:
        for tbl in section.header.tables:
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells]
                for i in range(0, len(cells) - 1, 2):
                    if "name of equipment" in cells[i].lower() and cells[i + 1]:
                        return cells[i + 1]
    return ""


def parse_ecr_document(path: str | Path) -> ParsedECR:
    path = Path(path)
    if path.suffix.lower() == ".doc":
        path = bmr.convert_doc_to_docx(path)
    try:
        doc = bmr._open_docx(path)
    except Exception as exc:
        raise bmr.BMRParseError(f"Couldn't read '{path.name}' as a Word document: {exc}") from exc

    equipment_name = _equipment_name_from_header(doc)
    if not equipment_name:
        raise bmr.BMRParseError(f"Couldn't find a 'Name of Equipment' field in '{path.name}'s header.")
    category, subtype = equipment_import._classify(equipment_name)

    steps: list[bmr.ParsedOperation] = []
    seen: set[tuple[str, str]] = set()
    for table in doc.tables:
        col_map = bmr._header_column_map(table)
        if col_map is None:
            continue
        op_col = col_map["operation"]
        for row in table.rows[bmr._data_start_row(table):]:
            cells = [c.text.strip() for c in row.cells]
            if not any(cells) or len(set(cells)) == 1:
                continue  # blank row, or a merged "Step No. X: ..." section header
            if op_col >= len(cells):
                continue
            text = cells[op_col]
            if not text:
                continue
            op_no = cells[0] if op_col != 0 else ""
            key = (op_no, text)
            if key in seen:
                continue  # duplicate "Checked By:" signature row for the same step
            seen.add(key)
            minutes, method = bmr._duration_from_text(text)
            steps.append(bmr.ParsedOperation(text=text, op_minutes=minutes, method=method))

    if not steps:
        raise bmr.BMRParseError(f"No cleaning-step table found in '{path.name}'.")
    return ParsedECR(equipment_name=equipment_name, category=category, subtype=subtype, steps=steps)
