"""Product recipes, stored in the database (tables ``recipe_sheets`` and
``recipe_stages``). One RecipeSheet per product; one RecipeStage per
operation, ordered by ``seq``:

    name                 -> operation text
    op_minutes           -> operation time (min)
    clean_minutes        -> cleaning time (min)
    equipment_ids        -> comma-separated equipment IDs allowed for the step
    temperature          -> standard temperature, free text ("60-65", "RT")
    actual_temperature   -> optional manually-set actual-temperature reference;
                            if blank the scheduler derives one from the standard
                            value, and either way a fresh +/-1-2 degree variant
                            is drawn per batch at schedule time.

This module is the only place that reads or writes those tables. It used to
be a single Excel workbook (Database/recipes.xlsx) — the public API here is
unchanged so callers didn't have to change.
"""
import re
from dataclasses import dataclass, field

from .db import SessionLocal
from .models import RecipeSheet, RecipeStage


@dataclass
class Stage:
    name: str
    op_minutes: float
    clean_minutes: float
    equipment_ids: list[str] = field(default_factory=list)
    temperature: str = ""
    actual_temperature: str = ""


def sheet_name_for(product_code: str) -> str:
    """Recipe-sheet identifier for a product code. Kept short and free of
    path/Excel-hostile characters — legacy constraint from the workbook days,
    retained so identifiers stay stable across the migration."""
    cleaned = re.sub(r"[\\/*?:\[\]]", "-", product_code).strip()
    return cleaned[:31] or "Product"


def list_product_sheets() -> list[str]:
    with SessionLocal() as session:
        return [r.name for r in session.query(RecipeSheet).order_by(RecipeSheet.name).all()]


def create_product_sheet(sheet_name: str, starter_stages: int = 1) -> None:
    with SessionLocal() as session:
        if session.get(RecipeSheet, sheet_name) is not None:
            raise ValueError(f"Recipe '{sheet_name}' already exists")
        session.add(RecipeSheet(name=sheet_name))
        for i in range(starter_stages):
            session.add(RecipeStage(
                sheet=sheet_name, seq=i + 1, name=f"Operation {i + 1}",
                op_minutes=0.0, clean_minutes=0.0, equipment_ids="",
                temperature="", actual_temperature="",
            ))
        session.commit()


def delete_product_sheet(sheet_name: str) -> None:
    with SessionLocal() as session:
        session.query(RecipeStage).filter(RecipeStage.sheet == sheet_name).delete()
        session.query(RecipeSheet).filter(RecipeSheet.name == sheet_name).delete()
        session.commit()


def read_recipe(sheet_name: str) -> list[Stage]:
    with SessionLocal() as session:
        if session.get(RecipeSheet, sheet_name) is None:
            raise ValueError(f"No recipe named '{sheet_name}'")
        rows = (
            session.query(RecipeStage)
            .filter(RecipeStage.sheet == sheet_name)
            .order_by(RecipeStage.seq)
            .all()
        )
        return [
            Stage(
                name=str(r.name),
                op_minutes=float(r.op_minutes or 0),
                clean_minutes=float(r.clean_minutes or 0),
                equipment_ids=[e.strip() for e in (r.equipment_ids or "").split(",") if e.strip()],
                temperature=str(r.temperature or ""),
                actual_temperature=str(r.actual_temperature or ""),
            )
            for r in rows
        ]


def write_recipe(sheet_name: str, stages: list[Stage]) -> None:
    """Overwrites the recipe with the supplied stage list, in order. Creates
    the RecipeSheet if it doesn't exist yet."""
    with SessionLocal() as session:
        if session.get(RecipeSheet, sheet_name) is None:
            session.add(RecipeSheet(name=sheet_name))
        session.query(RecipeStage).filter(RecipeStage.sheet == sheet_name).delete()
        session.flush()
        for i, stage in enumerate(stages):
            session.add(RecipeStage(
                sheet=sheet_name, seq=i + 1, name=stage.name or f"Operation {i + 1}",
                op_minutes=stage.op_minutes, clean_minutes=stage.clean_minutes,
                equipment_ids=", ".join(stage.equipment_ids),
                temperature=stage.temperature, actual_temperature=stage.actual_temperature,
            ))
        session.commit()
