"""Cleaning-procedure templates, stored in the database (tables
``ecr_template_sheets`` and ``ecr_template_steps``). One EcrTemplate per
equipment *category* (or category+subtype, for categories with more than one
ECR document — e.g. "Dryer" has separate Vacuum Tray and FBD procedures);
one EcrTemplateStep per cleaning step, ordered by ``seq`` (just a name and a
duration — cleaning steps carry no equipment IDs or temperature).

This mirrors recipes.py. It used to be a single Excel workbook
(Database/ecr_templates.xlsx) — the public API is unchanged.
"""
import re

from .db import SessionLocal
from .models import EcrTemplate, EcrTemplateStep


class TemplateStep:
    __slots__ = ("name", "op_minutes")

    def __init__(self, name: str, op_minutes: float):
        self.name = name
        self.op_minutes = op_minutes


def cleaning_key_for(category: str, subtype: str | None) -> str:
    """Categories with only one ECR document are keyed by category alone;
    categories with more than one (currently just Dryer: Vacuum Tray vs
    FBD) are disambiguated by subtype too."""
    if category == "Dryer" and subtype:
        return f"{category}-{subtype}"
    return category


def sheet_name_for(key: str) -> str:
    """Legacy identifier normaliser, retained so keys stay stable across the
    migration from the workbook."""
    cleaned = re.sub(r"[\\/*?:\[\]]", "-", key).strip()
    return cleaned[:31] or "Category"


def list_template_sheets() -> list[str]:
    with SessionLocal() as session:
        return [r.key for r in session.query(EcrTemplate).order_by(EcrTemplate.key).all()]


def read_template(key: str) -> list[TemplateStep]:
    """Returns [] if this category has no template yet."""
    with SessionLocal() as session:
        rows = (
            session.query(EcrTemplateStep)
            .filter(EcrTemplateStep.template_key == key)
            .order_by(EcrTemplateStep.seq)
            .all()
        )
        return [TemplateStep(name=str(r.name), op_minutes=float(r.op_minutes or 0)) for r in rows]


def write_template(key: str, steps: list[TemplateStep]) -> None:
    """Overwrites this category's template with the supplied step list, in order."""
    with SessionLocal() as session:
        if session.get(EcrTemplate, key) is None:
            session.add(EcrTemplate(key=key))
        session.query(EcrTemplateStep).filter(EcrTemplateStep.template_key == key).delete()
        session.flush()
        for i, step in enumerate(steps):
            session.add(EcrTemplateStep(
                template_key=key, seq=i + 1, name=step.name or f"Step {i + 1}",
                op_minutes=step.op_minutes,
            ))
        session.commit()
