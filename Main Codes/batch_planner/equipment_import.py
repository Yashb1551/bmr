"""Equipment list import — reads the plant's master equipment annexure
(.docx, or legacy .doc via Word COM conversion) into equipment rows: name,
capacity, MOC, real ID, and floor (read from "Floor-<name>" section-header
rows that split the source table)."""
import re
from dataclasses import dataclass
from pathlib import Path

from . import bmr  # reuses .doc conversion, folder scan, docx opening, and BMRParseError

_CAPACITY_LITRE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(KL|L|Lit)\b", re.IGNORECASE)
_FLOOR_FIXES = {"forth floor": "Fourth Floor"}

# (substring to match in the equipment name, category, subtype) — includes
# tolerance for real-document typos seen across different plant blocks'
# annexures (e.g. "Glass Line Reactor", "Nutche Filter", "Mutimill").
CATEGORY_RULES: list[tuple[str, str, str | None]] = [
    ("glass lined", "Reactor", "GLR"),
    ("glass line reactor", "Reactor", "GLR"),
    ("srp reactor", "Reactor", "SSR"),
    ("ss reactor", "Reactor", "SSR"),
    ("multi-mill", "Multi Mill", None),
    ("multi mill", "Multi Mill", None),
    ("mutimill", "Multi Mill", None),
    ("muti-mill", "Multi Mill", None),
    ("muti mill", "Multi Mill", None),
    ("jet mill", "Jet Mill", None),
    ("sifter", "Sifter", None),
    ("conta blender", "Blender", None),
    ("blender", "Blender", None),
    ("pressure nutsche filter", "Nutsche Filter", None),
    ("nutsche filter", "Nutsche Filter", None),
    ("nutche filter", "Nutsche Filter", None),
    ("sparkler filter", "Sparkler Filter", None),
    ("centrifuge", "Centrifuge", None),
    ("vacuum tray dryer", "Dryer", "Vacuum Tray"),
    ("fluid bed dryer", "Dryer", "FBD"),
    ("candle filter", "Candle Filter", None),
    ("vacuum pump", "Vacuum Pump", None),
    ("weighing balance", "Weighing Balance", None),
]


@dataclass
class ParsedEquipment:
    equipment_id: str
    name: str
    category: str
    subtype: str | None
    capacity_text: str
    capacity_l: float | None
    moc: str
    area: str


def _classify(name: str) -> tuple[str, str | None]:
    lower = name.lower()
    for substr, category, subtype in CATEGORY_RULES:
        if substr in lower:
            return category, subtype
    return name.strip() or "Other", None


def _capacity_to_litres(text: str) -> float | None:
    m = _CAPACITY_LITRE_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    return value * 1000 if m.group(2).lower() == "kl" else value


def _normalize_floor(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    if "-" in text:
        text = text.split("-", 1)[-1].strip()
    return _FLOOR_FIXES.get(text.lower(), text.title())


def _header_columns(header_cells: list[str]) -> dict[str, int]:
    col: dict[str, int] = {}
    for idx, h in enumerate(header_cells):
        if "name" in h:
            col.setdefault("name", idx)
        elif "capacity" in h:
            col.setdefault("capacity", idx)
        elif "moc" in h:
            col.setdefault("moc", idx)
        elif h == "id" or ("equipment" in h and "id" in h):
            col.setdefault("id", idx)
    return col


def parse_equipment_document(path: str | Path) -> list[ParsedEquipment]:
    path = Path(path)
    if path.suffix.lower() == ".doc":
        path = bmr.convert_doc_to_docx(path)
    try:
        doc = bmr._open_docx(path)
    except Exception as exc:
        raise bmr.BMRParseError(f"Couldn't read '{path.name}' as a Word document: {exc}") from exc

    results: list[ParsedEquipment] = []
    for table in doc.tables:
        if len(table.rows) < 2:
            continue
        header = [c.text.strip().lower() for c in table.rows[0].cells]
        col = _header_columns(header)
        if "name" not in col or "id" not in col:
            continue

        current_floor = "Unknown"
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if not any(cells):
                continue
            if len(set(cells)) == 1 and "floor" in cells[0].lower():
                current_floor = _normalize_floor(cells[0])
                continue
            eid = cells[col["id"]] if col["id"] < len(cells) else ""
            name = cells[col["name"]] if col["name"] < len(cells) else ""
            if not eid or not name:
                continue
            capacity_text = cells[col["capacity"]] if "capacity" in col and col["capacity"] < len(cells) else ""
            moc = cells[col["moc"]] if "moc" in col and col["moc"] < len(cells) else ""
            category, subtype = _classify(name)
            results.append(ParsedEquipment(
                equipment_id=eid, name=name, category=category, subtype=subtype,
                capacity_text=capacity_text, capacity_l=_capacity_to_litres(capacity_text),
                moc=moc, area=current_floor,
            ))

    if not results:
        raise bmr.BMRParseError(f"No equipment table found in '{path.name}'.")
    return results
