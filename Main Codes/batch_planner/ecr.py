"""Equipment Cleaning Record (ECR) generation.

For every piece of equipment used in a batch, its cleaning procedure starts
the moment its *last* use in that batch ends (mirrors the production
schedule: end of equipment usage throughout the batch = start of cleaning),
computed step by step from that equipment category's imported ECR template
(ecr_templates.py), with the same kind of +/-5% per-step random variance a
BMR applies to its thermal operations — see STEP_VARIANCE.

Like Allocation, the generated steps are computed once at schedule time and
persisted (CleaningStep rows) rather than re-rolled on every view — the
random variance has to be applied exactly once, or the same batch's cleaning
times would silently change every time the page re-renders.

This is a reporting/documentation layer only: it doesn't change what the
scheduler considers "free" equipment (Allocation.clean_end, from the
recipe's simple Cleaning Time field, still governs booking availability) —
the ECR log is the fuller, real-world cleaning paperwork trail, generally
longer than that scheduling buffer, and counted once per equipment actually
used in the batch rather than once per recipe stage.

Once cleaned, equipment is considered "held clean" for CLEAN_HOLD_HOURS;
past that, the Equipment Cleaning Schedule (equipment_cleaning_schedule())
flags it as needing to be cleaned again before its next use.
"""
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import bmr, ecr_templates
from .models import Allocation, Batch, CleaningStep, Equipment, product_display_names

CLEAN_HOLD_HOURS = 72
STEP_VARIANCE = 0.05  # +/-5% per cleaning step, drawn independently for each


@dataclass
class ECRStepResult:
    seq: int
    text: str
    op_start: datetime
    op_end: datetime


@dataclass
class EquipmentECR:
    equipment_id: str
    category: str
    last_operation: str
    last_op_no: int | None
    cleaning_start: datetime
    steps: list[ECRStepResult] = field(default_factory=list)
    template_found: bool = True

    @property
    def cleaning_end(self) -> datetime:
        return self.steps[-1].op_end if self.steps else self.cleaning_start

    @property
    def held_clean_until(self) -> datetime:
        return self.cleaning_end + timedelta(hours=CLEAN_HOLD_HOURS)


def _build_steps(key: str, start: datetime, fallback_minutes: float) -> tuple[list[dict], bool]:
    """Returns (step dicts, template_found). Falls back to a single generic
    step using the recipe's own Cleaning Time when this equipment category
    has no imported ECR template yet, so the log still shows *something*."""
    template = ecr_templates.read_template(key)
    if not template:
        # No template and no cleaning time on the recipe either -> fall back to
        # the standard 5 min +/-2% for an undefined duration rather than showing
        # nothing.
        minutes = fallback_minutes if fallback_minutes > 0 else bmr.resolve_op_minutes(0)
        end = start + timedelta(minutes=round(minutes))
        return [{"name": "Equipment Cleaning", "start": start, "end": end}], False

    steps: list[dict] = []
    cursor = start
    for step in template:
        if step.op_minutes and step.op_minutes > 0:
            minutes = round(step.op_minutes * random.uniform(1 - STEP_VARIANCE, 1 + STEP_VARIANCE))
        else:
            # Step with no time defined -> 5 min +/-2%, re-drawn each generation.
            minutes = round(bmr.resolve_op_minutes(0), 2)
        step_end = cursor + timedelta(minutes=minutes)
        steps.append({"name": step.name, "start": cursor, "end": step_end})
        cursor = step_end
    return steps, True


def generate_cleaning_steps_for_batch(session: Session, batch_id: int) -> None:
    """Computes and persists this batch's CleaningStep rows — one cleaning
    sequence per distinct piece of equipment used, starting at that
    equipment's last use in the batch. Called right after a batch's
    Allocations are created (schedule_order / reschedule_batch); safe to
    call again after a reschedule since the caller clears old rows first via
    the batch's cascade delete."""
    allocations = session.query(Allocation).filter(Allocation.batch_id == batch_id).all()
    by_equipment: dict[str, list[Allocation]] = {}
    for a in allocations:
        by_equipment.setdefault(a.equipment_id, []).append(a)

    for equipment_id, allocs in by_equipment.items():
        # Break ties on op_end (e.g. several zero-duration "label" stages that share
        # a timestamp) by stage_seq, so this is truly the equipment's last use in the
        # batch's operation order, not just whichever tied allocation was queried first.
        last = max(allocs, key=lambda a: (a.op_end, a.stage_seq))
        equipment = session.get(Equipment, equipment_id)
        category = equipment.category if equipment else "Other"
        subtype = equipment.subtype if equipment else None
        key = ecr_templates.cleaning_key_for(category, subtype)
        fallback_minutes = (last.clean_end - last.op_end).total_seconds() / 60
        steps, _ = _build_steps(key, last.op_end, fallback_minutes)
        for seq, step in enumerate(steps, start=1):
            session.add(CleaningStep(
                batch_id=batch_id, equipment_id=equipment_id, seq=seq,
                step_name=step["name"], step_start=step["start"], step_end=step["end"],
                last_operation=last.stage_name, last_op_no=last.stage_seq,
            ))


def ecr_log_for_batch(session: Session, batch_id: int) -> list[EquipmentECR]:
    """One entry per distinct piece of equipment used in this batch, from
    its already-persisted CleaningStep rows, in the order that equipment's
    usage in the batch ended."""
    steps = (
        session.query(CleaningStep)
        .filter(CleaningStep.batch_id == batch_id)
        .order_by(CleaningStep.equipment_id, CleaningStep.seq)
        .all()
    )
    equipment_ids = {s.equipment_id for s in steps}
    categories = {e.id: e.category for e in session.query(Equipment).filter(Equipment.id.in_(equipment_ids)).all()}

    by_equipment: dict[str, list[CleaningStep]] = {}
    for s in steps:
        by_equipment.setdefault(s.equipment_id, []).append(s)

    entries: list[EquipmentECR] = []
    for equipment_id, group in by_equipment.items():
        group.sort(key=lambda s: s.seq)
        entries.append(EquipmentECR(
            equipment_id=equipment_id,
            category=categories.get(equipment_id, "Other"),
            last_operation=group[0].last_operation,
            last_op_no=group[0].last_op_no,
            cleaning_start=group[0].step_start,
            steps=[ECRStepResult(s.seq, s.step_name, s.step_start, s.step_end) for s in group],
        ))
    entries.sort(key=lambda e: e.cleaning_start)
    return entries


@dataclass
class EquipmentCleaningStatus:
    equipment_id: str
    category: str
    area: str
    last_batch_label: str | None
    cleaning_start: datetime | None
    cleaning_end: datetime | None
    held_clean_until: datetime | None
    status: str  # "Never Used" / "Running" / "Cleaning" / "Clean" / "Needs Cleaning"
    hours_info: str
    next_use_start: datetime | None
    next_use_flag: bool


def equipment_cleaning_schedule(session: Session, as_of: datetime) -> list[EquipmentCleaningStatus]:
    from . import scheduler  # local import: scheduler.py doesn't import ecr.py, avoid a cycle

    results: list[EquipmentCleaningStatus] = []
    product_labels_by_code = product_display_names(session)
    equipment_list = (
        session.query(Equipment)
        .filter(Equipment.status == "Active")
        .order_by(Equipment.category, Equipment.id)
        .all()
    )
    for e in equipment_list:
        live_status = scheduler.equipment_status_at(session, e, as_of)
        current_alloc = live_status["allocation"]

        last_step = (
            session.query(CleaningStep)
            .filter(CleaningStep.equipment_id == e.id, CleaningStep.step_end <= as_of)
            .order_by(CleaningStep.step_end.desc())
            .first()
        )
        next_alloc = (
            session.query(Allocation)
            .filter(Allocation.equipment_id == e.id, Allocation.op_start > as_of)
            .order_by(Allocation.op_start)
            .first()
        )

        cleaning_start = cleaning_end = held_clean_until = None
        last_batch_label = None

        if current_alloc is not None:
            status = live_status["status"]  # "Running" or "Cleaning"
            batch = session.get(Batch, current_alloc.batch_id)
            product_label = product_labels_by_code.get(batch.product_code, batch.product_code)
            hours_info = f"Currently {status.lower()} — batch {batch.batch_number} ({product_label})"
        elif last_step is None:
            status = "Never Used"
            hours_info = "No cleaning history yet"
        else:
            cleaning_end = last_step.step_end
            held_clean_until = cleaning_end + timedelta(hours=CLEAN_HOLD_HOURS)
            first_step = (
                session.query(CleaningStep)
                .filter(CleaningStep.batch_id == last_step.batch_id, CleaningStep.equipment_id == e.id)
                .order_by(CleaningStep.seq)
                .first()
            )
            cleaning_start = first_step.step_start if first_step else cleaning_end
            batch = session.get(Batch, last_step.batch_id)
            product_label = product_labels_by_code.get(batch.product_code, batch.product_code)
            last_batch_label = batch.label or f"Batch {batch.batch_number} ({product_label})"
            hours_left = (held_clean_until - as_of).total_seconds() / 3600
            if hours_left > 0:
                status = "Clean"
                hours_info = f"Held clean for {hours_left:.0f} more hour(s)"
            else:
                status = "Needs Cleaning"
                hours_info = f"Clean-hold expired {abs(hours_left):.0f} hour(s) ago"

        next_use_flag = bool(held_clean_until and next_alloc and next_alloc.op_start > held_clean_until)

        results.append(EquipmentCleaningStatus(
            equipment_id=e.id, category=e.category, area=e.area, last_batch_label=last_batch_label,
            cleaning_start=cleaning_start, cleaning_end=cleaning_end, held_clean_until=held_clean_until,
            status=status, hours_info=hours_info,
            next_use_start=next_alloc.op_start if next_alloc else None, next_use_flag=next_use_flag,
        ))
    return results
