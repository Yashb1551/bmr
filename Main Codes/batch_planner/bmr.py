"""Batch Manufacturing Record (BMR) support.

Three directions:
  1. parse_bmr_document() reads a whole BMR file (.docx, or .doc via Word
     COM conversion) end to end: product code/name/batch size from the
     page header table, and every operation from every "Operation" table
     in the body (real BMRs split process steps across several tables —
     Manufacturing Process, Multi Mill, Blending, etc. — not just one).
     For each operation:
       - Equipment is read from real equipment codes appearing anywhere in
         that row (bracketed like "[PR/API/SSR/01]" or bare like
         "MA/B2/E/026") and carries forward to later operations until a
         different code appears — real BMRs only restate it when the
         equipment changes.
       - Standard temperature is read from the row's "Std." column, if any.
       - Duration is derived from the operation text, checked in this order:
           1. an explicit duration ("for 8 hours", "for one hour")
           2. a quantity in kg (200 kg = 60 min, 0.3 min/kg)
           3. a volume in L (400 L = 25 min, 0.0625 min/L)
           4. an analysis/QC mention (LOD = 60 min, Moisture = 40 min,
              otherwise 300 min for a complete analysis)
           5. a heating/chilling/cooling mention (120 min default)
           6. a charge/load/unload mention (5 min)
           7. a "check ..." mention (20 min)
           8. otherwise 0, flagged for manual entry
  2. scan_folder() lists BMR files in a folder that aren't linked to a
     product yet, for the Products page "import" UI.

Standard Temperature is only accepted from the source document when it
actually looks like a temperature (a number, range, or a short token like
"RT") — other text landing in that column (remarks, merged-cell bleed-over)
is discarded rather than stored as if it were a temperature. Cleaning time
only defaults to a nonzero value when the operation text itself mentions
"clean"/"cleaning" — otherwise it's left at 0 rather than guessed. Every
computed duration is rounded to a whole minute; nothing here ever produces
a fractional-minute value.
"""
import io
import posixpath
import random
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document

_RELATIONSHIP_RE = re.compile(rb"<Relationship\b[^>]*/>")
_TARGET_ATTR_RE = re.compile(rb'\bTarget="([^"]*)"')
_TARGET_MODE_EXTERNAL_RE = re.compile(rb'\bTargetMode="External"')


def _open_docx(source) -> Document:
    """Opens a .docx as a python-docx Document, tolerating a Word quirk seen
    in some real documents: a relationship (e.g. a "high-def photo" variant
    of an image) whose Target is "NULL" or otherwise points at a part that
    isn't actually in the archive. python-docx errors on that with a raw
    `KeyError` from the zip reader — this strips any such dangling
    relationship first so the rest of the document (tables, text) still
    reads normally."""
    raw = source.read() if hasattr(source, "read") else Path(source).read_bytes()
    try:
        return Document(io.BytesIO(raw))
    except KeyError:
        pass

    with zipfile.ZipFile(io.BytesIO(raw)) as zin:
        names = set(zin.namelist())
        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".rels"):
                    base_dir = posixpath.dirname(posixpath.dirname(item.filename))  # "word/_rels/x.rels" -> "word"

                    def _strip_dangling(m):
                        target_match = _TARGET_ATTR_RE.search(m.group(0))
                        if not target_match or _TARGET_MODE_EXTERNAL_RE.search(m.group(0)):
                            return m.group(0)
                        target = target_match.group(1).decode("utf-8", errors="replace")
                        resolved = posixpath.normpath(posixpath.join(base_dir, target))
                        return m.group(0) if resolved in names else b""

                    data = _RELATIONSHIP_RE.sub(_strip_dangling, data)
                zout.writestr(item, data)
        out_buf.seek(0)
        return Document(out_buf)

KG_TO_MINUTES = 60 / 200        # 200 kg = 60 min, i.e. 0.3 min per kg
LITRE_TO_MINUTES = 25 / 400     # 400 L = 25 min, i.e. 0.0625 min per litre
ANALYSIS_LOD_MINUTES = 60.0
ANALYSIS_MOISTURE_MINUTES = 40.0
ANALYSIS_COMPLETE_MINUTES = 300.0
THERMAL_DEFAULT_MINUTES = 120.0
CHARGE_DEFAULT_MINUTES = 5.0
CHECK_DEFAULT_MINUTES = 20.0
DEFAULT_CLEANING_MINUTES = 15.0  # applied to newly-imported operations that use equipment

# A stand-in for an operation/cleaning step the master BMR leaves with no
# duration at all. Such a row still reserves equipment, and a zero-length
# reservation is meaningless, so it gets this fixed nominal length. It is a
# constant, not a draw: the master BMR's time periods are what every batch
# follows, so the same recipe must always produce the same schedule. A row
# that matters should have its real time set on the Products page rather than
# relying on this. Used by the scheduler (BMR operations) and ecr.py
# (cleaning steps).
UNDEFINED_OP_MINUTES = 5.0


def resolve_op_minutes(op_minutes: float) -> float:
    """`op_minutes` if it's a real positive duration, otherwise the fixed
    stand-in above. Deterministic — the same input always gives the same
    output, for every batch."""
    if op_minutes and op_minutes > 0:
        return op_minutes
    return UNDEFINED_OP_MINUTES

_WORD_NUMBERS = {
    "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUMBER_ALTERNATION = r"(\d+(?:\.\d+)?|" + "|".join(_WORD_NUMBERS) + r")"

_DURATION_RE = re.compile(
    r"\bfor\s+(?:a\s+)?" + _NUMBER_ALTERNATION + r"\s*(hours?|hrs?|min(?:ute)?s?)",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*kgs?\b", re.IGNORECASE)
_VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ltrs?\.?|liters?|litres?|l)\b", re.IGNORECASE)
_ANALYSIS_RE = re.compile(r"\b(analy[sz]e|analysis)\b", re.IGNORECASE)
_THERMAL_RE = re.compile(r"\b(heat|heating|chill|chilling|cool|cooling|warm|warming)\b", re.IGNORECASE)
_CHARGE_RE = re.compile(r"\b(charge|charged|load|loaded|unload|onload)\b", re.IGNORECASE)
_CHECK_RE = re.compile(r"\bcheck\b", re.IGNORECASE)

_EQUIPMENT_CODE_RE = re.compile(r"\[?([A-Za-z0-9]+(?:/[A-Za-z0-9]+){2,4})\]?")
_BRACKET_ONLY_RE = re.compile(r"^\[.*\]$")
_SECTION_HEADER_RE = re.compile(r"^stage[\s\-–]*[ivx\d]+\.?$", re.IGNORECASE)


def _is_section_header(text: str) -> bool:
    """True for rows that are just a section label ("Stage-II") rather than
    a real operation — real BMRs sometimes put these in the Operation column
    as a heading row within the table."""
    return bool(_SECTION_HEADER_RE.match(text.strip()))


_FILENAME_STAGE_RE = re.compile(r"stage[\s\-]*([ivxIVX]+|\d+)\b", re.IGNORECASE)
_FILENAME_FINAL_RE = re.compile(r"\bfinal\b", re.IGNORECASE)


def _stage_suffix_from_filename(filename: str) -> str:
    """A product's own "Product Name" header field is usually just the bare
    substance name ("Bilastine") even when the file is one stage of a
    multi-stage synthesis — the Product Code disambiguates (e.g. "A004" vs
    "A004/I") but doesn't read as obviously to a person scanning a product
    list. If the filename says which stage this is ("...stage-I...",
    "...Stage-II & final..."), surface that in the name too."""
    m = _FILENAME_STAGE_RE.search(filename)
    if not m:
        return ""
    suffix = f"Stage {m.group(1).upper()}"
    if _FILENAME_FINAL_RE.search(filename):
        suffix += " & Final"
    return suffix


class BMRParseError(Exception):
    pass


@dataclass
class ParsedOperation:
    text: str
    op_minutes: float
    method: str  # how the duration was derived, shown to the admin for review
    equipment_ids: list[str] = field(default_factory=list)
    temperature: str = ""


@dataclass
class ParsedBMR:
    product_code: str
    product_name: str
    batch_size_kg: float | None
    batch_no_prefix: str
    operations: list[ParsedOperation]


def _number_from_match(raw: str) -> float:
    raw = raw.lower()
    return _WORD_NUMBERS[raw] if raw in _WORD_NUMBERS else float(raw)


def _duration_from_text(text: str) -> tuple[float, str]:
    """Every path returns a whole number of minutes — no fractional times
    anywhere downstream, from the recipe grid through to the schedule."""
    duration_match = _DURATION_RE.search(text)
    if duration_match:
        value = _number_from_match(duration_match.group(1))
        unit = duration_match.group(2).lower()
        minutes = value * 60 if unit.startswith("h") else value
        return round(minutes), f"Explicit duration: {value:g} {unit}"

    qty_match = _QUANTITY_RE.search(text)
    if qty_match:
        qty_kg = float(qty_match.group(1))
        minutes = qty_kg * KG_TO_MINUTES
        return round(minutes), f"Quantity-based: {qty_kg:g} kg × {KG_TO_MINUTES:g} min/kg"

    vol_match = _VOLUME_RE.search(text)
    if vol_match:
        qty_l = float(vol_match.group(1))
        minutes = qty_l * LITRE_TO_MINUTES
        return round(minutes), f"Volume-based: {qty_l:g} L × {LITRE_TO_MINUTES:g} min/L"

    if _ANALYSIS_RE.search(text):
        lower = text.lower()
        if "lod" in lower:
            return round(ANALYSIS_LOD_MINUTES), "Default: LOD analysis"
        if "moisture" in lower:
            return round(ANALYSIS_MOISTURE_MINUTES), "Default: Moisture content analysis"
        return round(ANALYSIS_COMPLETE_MINUTES), "Default: Complete analysis"

    if _THERMAL_RE.search(text):
        return round(THERMAL_DEFAULT_MINUTES), "Default: heating/chilling"

    if _CHARGE_RE.search(text):
        return round(CHARGE_DEFAULT_MINUTES), "Default: charge/load"

    if _CHECK_RE.search(text):
        return round(CHECK_DEFAULT_MINUTES), "Default: equipment/line check"

    return 0.0, "Not detected — set duration manually"


def _extract_equipment_codes(text: str) -> list[str]:
    codes = []
    for m in _EQUIPMENT_CODE_RE.finditer(text):
        code = m.group(1)
        # excludes pure-numeric matches like dates; de-dupes repeats from merged table
        # cells, whose text shows up once per underlying cell in the span.
        if sum(c.isalpha() for c in code) >= 2 and code not in codes:
            codes.append(code)
    return codes


_CLEAN_MENTION_RE = re.compile(r"\bclean\w*\b", re.IGNORECASE)  # clean, cleaning, cleanliness, cleaned, ...


def default_clean_minutes(text: str, equipment_ids: list[str]) -> float:
    """Starting cleaning/changeover time for a freshly-imported operation —
    only set when the operation text actually mentions "clean"/"cleaning"
    (otherwise there's no basis to assume this step involves cleaning at
    all) and only once real equipment is being reserved. Blank (0)
    otherwise. Admins can tune this per-operation afterward in the recipe
    editor; re-importing the same BMR never overwrites a saved edit
    (imports only run when you explicitly ask for them)."""
    if equipment_ids and _CLEAN_MENTION_RE.search(text):
        return DEFAULT_CLEANING_MINUTES
    return 0.0


_RANGE_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)")
_SINGLE_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)")
_TEMP_ALLOWED_WORDS = {
    "below", "above", "max", "maximum", "min", "minimum", "nmt", "nlt",
    "rt", "deg", "degc", "degree", "degrees", "ambient", "room",
}
_TEMP_KNOWN_TOKENS = {"rt", "ambient", "room temp", "room temperature"}


def _looks_like_temperature(text: str) -> bool:
    """True only for text that's plausibly a temperature spec — a number or
    range, optionally with a unit/qualifier word ("Below 45", "60-65 degC"),
    or a short known token ("RT"). Rejects remarks, names, or anything else
    that isn't actually a temperature, however it ended up in that column
    (e.g. a merged cell bleeding in unrelated text like "QC is checking")."""
    t = text.strip().lower().replace("°", "")
    if not t:
        return False
    if t in _TEMP_KNOWN_TOKENS:
        return True
    if not any(c.isdigit() for c in t) or len(t) > 20:
        return False
    words = re.findall(r"[a-zA-Z]{3,}", t)
    return all(w in _TEMP_ALLOWED_WORDS for w in words)


def compute_actual_temperature(standard_temperature: str, preferred_actual: str = "") -> str:
    """A plausible 'Actual' temperature for one batch's execution record. If
    the recipe has a manually-set Actual Temperature reference
    (`preferred_actual`), that's used as the base; otherwise the Standard
    Temperature is. Either way: a range ("60-65") draws uniformly within it,
    a single value ("25") jitters by 1-2 degrees, and non-numeric text
    ("RT") passes through unchanged. Call this once per (batch, operation) —
    it's meant to vary batch to batch, not be reused across a whole order."""
    text = (preferred_actual or standard_temperature or "").strip()
    if not text:
        return ""

    range_match = _RANGE_TEMP_RE.search(text)
    if range_match:
        low, high = float(range_match.group(1)), float(range_match.group(2))
        if low > high:
            low, high = high, low
        return f"{random.uniform(low, high):.1f}"

    single_match = _SINGLE_TEMP_RE.search(text)
    if single_match:
        base = float(single_match.group(1))
        jitter = random.uniform(1.0, 2.0) * random.choice([-1, 1])
        return f"{base + jitter:.1f}"

    return text


def parse_operations(texts: list[str]) -> list[ParsedOperation]:
    """Shared logic for a flat list of raw Operation-column strings (in
    order), with equipment codes carried forward from the last mention."""
    operations: list[ParsedOperation] = []
    current_equipment: list[str] = []
    for text in texts:
        text = text.strip()
        if not text or _is_section_header(text):
            continue
        codes = _extract_equipment_codes(text)
        if codes:
            current_equipment = codes
        minutes, method = _duration_from_text(text)
        operations.append(ParsedOperation(
            text=text, op_minutes=minutes, method=method,
            equipment_ids=list(current_equipment),
        ))
    return operations


# --- .doc -> .docx conversion (Word COM automation, Windows + MS Word only) ---

def convert_doc_to_docx(path: Path) -> Path:
    try:
        import win32com.client as win32
    except ImportError as exc:
        raise BMRParseError(
            "This is a legacy .doc file. Reading it requires Microsoft Word to be "
            "installed on this machine (used via COM automation) — pywin32 isn't available."
        ) from exc

    cache_dir = path.parent / "_converted"
    cache_dir.mkdir(exist_ok=True)
    out_path = cache_dir / (path.stem + ".docx")
    if out_path.exists() and out_path.stat().st_mtime >= path.stat().st_mtime:
        return out_path

    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Open(str(path), ReadOnly=True)
        doc.SaveAs(str(out_path), FileFormat=16)  # 16 = wdFormatDocumentDefault (.docx)
        doc.Close(False)
    except Exception as exc:
        raise BMRParseError(f"Word couldn't convert '{path.name}': {exc}") from exc
    finally:
        word.Quit()
    return out_path


# --- whole-document parsing ---

def _extract_header_info(doc: Document) -> dict:
    info: dict[str, str] = {}
    for section in doc.sections:
        for tbl in section.header.tables:
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells]
                for i in range(0, len(cells) - 1, 2):
                    label, value = cells[i].lower(), cells[i + 1]
                    if not value:
                        continue
                    if "product name" in label:
                        info["product_name"] = value
                    elif "product code" in label:
                        info["product_code"] = value
                    elif "batch size" in label:
                        info["batch_size_raw"] = value
                    elif label.startswith("batch no"):
                        info["batch_no_prefix"] = value
    return info


def _header_column_map(table) -> dict[str, int] | None:
    col_map: dict[str, int] = {}
    for row in table.rows[:2]:
        for idx, cell in enumerate(row.cells):
            text = cell.text.strip().lower()
            if text == "operation":
                col_map["operation"] = idx
            elif text in ("std.", "std"):
                col_map["temperature"] = idx
    return col_map if "operation" in col_map else None


def _data_start_row(table) -> int:
    if len(table.rows) > 1:
        second_row_labels = {c.text.strip().lower() for c in table.rows[1].cells}
        if second_row_labels & {"std.", "std", "initial", "final", "actual", "done by"}:
            return 2
    return 1


def parse_bmr_document(path: str | Path) -> ParsedBMR:
    """Reads a whole BMR file (.doc or .docx) — header info plus every
    operation across every operation-shaped table in the document."""
    path = Path(path)
    if path.suffix.lower() == ".doc":
        path = convert_doc_to_docx(path)

    try:
        doc = _open_docx(path)
    except Exception as exc:
        raise BMRParseError(f"Couldn't read '{path.name}' as a Word document: {exc}") from exc

    header = _extract_header_info(doc)

    operations: list[ParsedOperation] = []
    current_equipment: list[str] = []
    for table in doc.tables:
        col_map = _header_column_map(table)
        if col_map is None:
            continue
        op_col = col_map["operation"]
        temp_col = col_map.get("temperature")
        for row in table.rows[_data_start_row(table):]:
            if op_col >= len(row.cells):
                continue
            text = row.cells[op_col].text.strip()
            if not text or _is_section_header(text):
                continue

            row_text = " ".join(c.text for c in row.cells)
            codes = _extract_equipment_codes(row_text)
            if codes:
                current_equipment = codes

            temperature = ""
            if temp_col is not None and temp_col < len(row.cells):
                temp_text = row.cells[temp_col].text.strip()
                if _looks_like_temperature(temp_text):
                    temperature = temp_text

            minutes, method = _duration_from_text(text)
            operations.append(ParsedOperation(
                text=text, op_minutes=minutes, method=method,
                equipment_ids=list(current_equipment), temperature=temperature,
            ))

    if not operations:
        raise BMRParseError(f"No operation table found in '{path.name}'.")

    batch_size = None
    if "batch_size_raw" in header:
        m = re.search(r"(\d+(?:\.\d+)?)", header["batch_size_raw"])
        if m:
            batch_size = float(m.group(1))

    product_name = header.get("product_name", path.stem)
    stage_suffix = _stage_suffix_from_filename(path.name)
    if stage_suffix and stage_suffix.lower() not in product_name.lower():
        product_name = f"{product_name} - {stage_suffix}"

    return ParsedBMR(
        product_code=header.get("product_code", path.stem),
        product_name=product_name,
        batch_size_kg=batch_size,
        batch_no_prefix=header.get("batch_no_prefix", ""),
        operations=operations,
    )


def scan_folder(folder: Path) -> list[Path]:
    """Recursively scans `folder` (and any subfolders, e.g. per-block "B2"/"B3"
    directories) for .doc/.docx files, skipping cache/hidden folders like the
    "_converted" directory used for .doc -> .docx conversion output."""
    if not folder.exists():
        return []
    return sorted(
        (
            p for p in folder.rglob("*")
            if p.is_file()
            and p.suffix.lower() in (".doc", ".docx")
            and not any(part.startswith("_") for part in p.relative_to(folder).parts[:-1])
        ),
        key=lambda p: p.relative_to(folder).parts,
    )


# --- legacy simple .docx parsing (single table, Operation in column 0) ---

def _find_operation_table(doc: Document):
    for table in doc.tables:
        if len(table.rows) < 2:
            continue
        header = table.rows[0].cells[0].text.strip().lower()
        if "operation" in header:
            return table, True
    if doc.tables:
        return doc.tables[0], False
    return None, False


def parse_bmr_docx(file) -> list[ParsedOperation]:
    """`file` is a file-like object (e.g. Streamlit's UploadedFile)."""
    try:
        doc = _open_docx(file)
    except Exception as exc:
        raise BMRParseError(f"Couldn't read this as a Word (.docx) file: {exc}") from exc

    table, header_matched = _find_operation_table(doc)
    if table is None:
        raise BMRParseError("No table found in this document. The BMR needs a table with "
                             "an 'Operation' column.")

    texts = [row.cells[0].text for row in table.rows[1:]]
    operations = parse_operations(texts)

    if not operations:
        raise BMRParseError("Found a table, but its first column had no operation text to read.")

    return operations
