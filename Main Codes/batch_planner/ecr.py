"""Equipment Cleaning Record (ECR) generation.

Equipment is dirty the moment a batch finishes with it, and has to be clean
again before whatever is booked on it next. The cleaning is scheduled into
that gap rather than onto the critical path: production moves straight to its
next operation, and the cleaning runs alongside — often while the batch is
busy on other equipment, sometimes a day or more ahead of the next use.

Where in the gap it lands is a balance of two pulls:

* as soon as possible after the equipment comes free, so the work is done
  while the plant has slack rather than against the clock;
* but finishing no more than CLEAN_HOLD_HOURS before the next booking,
  because a clean is only held good for that long — cleaning a vessel a week
  before it is needed just means cleaning it twice.

So cleaning follows the use directly when the next booking is near, and is
held back to land inside the clean-hold when the next booking is far off.
Where the gap is too tight for the full procedure, it is placed to finish as
close to the next booking as the equipment coming free allows.

The steps come from that equipment category's imported ECR template
(ecr_templates.py). Each step runs for exactly the time the master template
gives it, so the same template always produces the same cleaning sequence,
batch after batch.

Like Allocation, the generated steps are computed once at schedule time and
persisted (CleaningStep rows) rather than recomputed on every view, so a
batch's recorded cleaning times stay fixed even if the template is later
edited — the log is a record of what was scheduled, not a live view.

This is a reporting/documentation layer only: it doesn't change what the
scheduler considers "free" equipment (Allocation.clean_end, from the recipe's
simple Cleaning Time field, still governs booking availability) — the ECR log
is the fuller, real-world cleaning paperwork trail, counted once per
equipment actually used in the batch rather than once per recipe stage.

Equipment is reported dirty from the moment it is used until its cleaning
finishes, then held clean for CLEAN_HOLD_HOURS; the Equipment Cleaning
Schedule (equipment_cleaning_schedule()) shows both.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import bmr, ecr_templates
from .models import Allocation, Batch, CleaningStep, Equipment, product_display_names

CLEAN_HOLD_HOURS = 72


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
    last_operation: str     # the batch operation this equipment ran before cleaning
    last_op_no: int | None  # that operation's BMR Op. No.
    cleaning_start: datetime
    steps: list[ECRStepResult] = field(default_factory=list)
    template_found: bool = True
    next_use_start: datetime | None = None  # next booking on this equipment, if any

    @property
    def ready_in_time(self) -> bool:
        """False when the equipment is booked again before its cleaning can
        finish — the gap the scheduler left is shorter than the cleaning
        procedure actually takes. Surfaced rather than hidden: the schedule
        is the thing that needs changing, not the paperwork."""
        return self.next_use_start is None or self.cleaning_end <= self.next_use_start

    @property
    def cleaning_end(self) -> datetime:
        return self.steps[-1].op_end if self.steps else self.cleaning_start

    @property
    def held_clean_until(self) -> datetime:
        return self.cleaning_end + timedelta(hours=CLEAN_HOLD_HOURS)


def _step_durations(key: str, fallback_minutes: float) -> tuple[list[tuple[str, int]], bool]:
    """(name, minutes) per cleaning step, and whether a real template was
    found. Falls back to a single generic step on the recipe's own Cleaning
    Time when this equipment category has no imported ECR template yet, so
    the log still shows *something*."""
    template = ecr_templates.read_template(key)
    if not template:
        minutes = fallback_minutes if fallback_minutes > 0 else bmr.resolve_op_minutes(0)
        return [("Equipment Cleaning", round(minutes))], False
    # Each step runs for exactly the time its ECR master template gives it —
    # no per-batch variance. A step the template leaves at 0 gets the fixed
    # stand-in rather than zero length.
    return [(st.name, round(bmr.resolve_op_minutes(st.op_minutes))) for st in template], True


def _clean_start(session: Session, equipment_id: str, dirty_from: datetime,
                  duration: timedelta) -> tuple[datetime, datetime | None]:
    """When to start cleaning this equipment, and the next booking it has to
    be ready for.

    The equipment is dirty from `dirty_from` — the moment this batch stops
    using it — and has to be clean again before whatever is booked on it
    next. The cleaning goes in that gap, so production moves to the next
    operation without waiting for cleaning to finish.

    Two pulls decide where in the gap it lands:

    * as soon as possible after use, so the work is done while the plant has
      the slack rather than against the clock;
    * but no more than CLEAN_HOLD_HOURS before the next booking, because a
      clean is only held good for that long — cleaning a vessel the week
      before it is needed just means cleaning it twice.

    So it starts right after use when the next booking is near, and is held
    back to land inside the clean-hold when the next booking is far off. With
    nothing booked after it, cleaning simply follows the use.
    """
    next_alloc = (
        session.query(Allocation)
        .filter(Allocation.equipment_id == equipment_id,
                Allocation.op_start >= dirty_from)
        .order_by(Allocation.op_start)
        .first()
    )
    if next_alloc is None:
        return dirty_from, None

    deadline = next_alloc.op_start
    start = max(dirty_from, deadline - timedelta(hours=CLEAN_HOLD_HOURS))
    if start + duration > deadline:
        # Gap too tight for the full procedure — finish as close to the
        # deadline as the equipment being free allows, and no earlier than
        # the moment it actually came free.
        start = max(dirty_from, deadline - duration)
    return start, deadline


def generate_cleaning_steps_for_batch(session: Session, batch_id: int) -> None:
    """Computes and persists this batch's CleaningStep rows — one cleaning
    sequence per distinct piece of equipment used, starting once this batch
    has finished with that equipment and finishing before whatever is booked
    on it next. Called right after a batch's Allocations are created
    (schedule_order / reschedule_batch); safe to call again after a
    reschedule since the caller clears old rows first via the batch's cascade
    delete."""
    allocations = session.query(Allocation).filter(Allocation.batch_id == batch_id).all()
    by_equipment: dict[str, list[Allocation]] = {}
    for a in allocations:
        by_equipment.setdefault(a.equipment_id, []).append(a)

    for equipment_id, allocs in by_equipment.items():
        # Break ties on op_end (e.g. several zero-duration "label" stages that
        # share a timestamp) by stage_seq, so this is truly the equipment's last
        # use in the batch's operation order, not whichever tied allocation was
        # queried first. The equipment is dirty from that moment.
        last = max(allocs, key=lambda a: (a.op_end, a.stage_seq))
        equipment = session.get(Equipment, equipment_id)
        category = equipment.category if equipment else "Other"
        subtype = equipment.subtype if equipment else None
        key = ecr_templates.cleaning_key_for(category, subtype)

        fallback_minutes = (last.clean_end - last.op_end).total_seconds() / 60
        durations, _ = _step_durations(key, fallback_minutes)
        total = timedelta(minutes=sum(minutes for _, minutes in durations))

        cursor, _deadline = _clean_start(session, equipment_id, last.op_end, total)
        for seq, (name, minutes) in enumerate(durations, start=1):
            step_end = cursor + timedelta(minutes=minutes)
            session.add(CleaningStep(
                batch_id=batch_id, equipment_id=equipment_id, seq=seq,
                step_name=name, step_start=cursor, step_end=step_end,
                last_operation=last.stage_name, last_op_no=last.stage_seq,
            ))
            cursor = step_end


def ecr_log_for_batch(session: Session, batch_id: int) -> list[EquipmentECR]:
    """One entry per distinct piece of equipment used in this batch, from
    its already-persisted CleaningStep rows, in the order the cleaning is
    carried out."""
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
        next_alloc = (
            session.query(Allocation)
            .filter(Allocation.equipment_id == equipment_id,
                    Allocation.op_start >= group[0].step_start)
            .order_by(Allocation.op_start)
            .first()
        )
        entries.append(EquipmentECR(
            equipment_id=equipment_id,
            category=categories.get(equipment_id, "Other"),
            last_operation=group[0].last_operation,
            last_op_no=group[0].last_op_no,
            cleaning_start=group[0].step_start,
            steps=[ECRStepResult(s.seq, s.step_name, s.step_start, s.step_end) for s in group],
            next_use_start=next_alloc.op_start if next_alloc else None,
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
        # Cleaning now runs before use, so the equipment is dirty again the
        # moment an operation has run on it — being inside the clean-hold is
        # not enough on its own.
        last_use = (
            session.query(Allocation)
            .filter(Allocation.equipment_id == e.id, Allocation.op_end <= as_of)
            .order_by(Allocation.op_end.desc())
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
            if last_use is not None and last_use.op_end > cleaning_end:
                status = "Needs Cleaning"
                hours_info = (f"Used since last cleaning "
                              f"(Op. No. {last_use.stage_seq} ended "
                              f"{last_use.op_end:%d/%m %H:%M})")
            elif hours_left > 0:
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
