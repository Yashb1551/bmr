"""Core, non-destructive batch scheduling engine.

Splitting an order into batches, and each batch into its recipe stages, then
placing every stage on the earliest available matching piece of equipment —
never touching anything already booked. Equipment stays blocked from
op_start through clean_end (operation + changeover/CIP time); the batch
itself moves to its next stage as soon as the operation finishes at
op_end, it doesn't wait for cleaning.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import bmr, ecr, recipes
from .models import Allocation, AuditLog, Batch, CleaningStep, Equipment, Order, Product


class SchedulingError(Exception):
    pass


@dataclass
class StageAllocationResult:
    stage_name: str
    equipment_id: str
    op_start: datetime
    op_end: datetime
    clean_end: datetime
    temperature: str = ""
    stage_seq: int = 0


@dataclass
class BatchResult:
    batch_number: int
    batch_id: int
    label: str | None = None
    allocations: list[StageAllocationResult] = field(default_factory=list)

    @property
    def completion(self) -> datetime:
        return self.allocations[-1].op_end


@dataclass
class OrderResult:
    order_id: int
    product_code: str
    quantity_kg: float
    num_batches: int
    batches: list[BatchResult]

    @property
    def completion(self) -> datetime:
        return max(b.completion for b in self.batches)


def _earliest_free_slot(session: Session, equipment_id: str, earliest_start: datetime,
                         duration_minutes: float) -> datetime:
    """Earliest moment >= earliest_start that `equipment_id` is free for
    `duration_minutes`, checking gaps between (and before/after) existing
    bookings on that equipment."""
    duration = timedelta(minutes=duration_minutes)
    existing = (
        session.query(Allocation)
        .filter(Allocation.equipment_id == equipment_id)
        .order_by(Allocation.op_start)
        .all()
    )
    candidate = earliest_start
    for alloc in existing:
        if alloc.clean_end <= candidate:
            continue  # this booking is already in the past relative to candidate
        if alloc.op_start >= candidate + duration:
            break  # candidate fits in the gap before this booking
        candidate = max(candidate, alloc.clean_end)  # push past this booking + its cleaning
    return candidate


def _batch_label(base_label: str | None, batch_num: int, num_batches: int) -> str | None:
    if not base_label:
        return None
    return base_label if num_batches == 1 else f"{base_label}-{batch_num}"


def _schedule_stages(session: Session, batch_id: int, recipe_sheet: str,
                      stages: list[recipes.Stage], ready_time: datetime) -> list[StageAllocationResult]:
    """Places every stage of one batch on the earliest available matching
    equipment, starting no earlier than `ready_time`. Shared by both a fresh
    schedule_order() call and a later reschedule_batch()."""
    stage_results: list[StageAllocationResult] = []
    for seq, stage in enumerate(stages, start=1):
        if not stage.equipment_ids:
            if stage.op_minutes == 0:
                # A zero-duration, no-equipment row is a label/checkpoint, not a real
                # operation (e.g. a "Stage-II" section heading that ended up in the
                # Operation column) — skip it rather than blocking the whole order.
                continue
            raise SchedulingError(
                f"Stage '{stage.name}' in recipe '{recipe_sheet}' has a duration "
                f"({stage.op_minutes:g} min) but no equipment IDs assigned"
            )

        # Every operation runs for exactly the time the master BMR recipe
        # gives it — no per-batch variance of any kind, so two batches of the
        # same product have identical operation durations. Equipment
        # availability never changes an operation's duration, only its start
        # time. (An operation the recipe leaves at 0 is the one exception; see
        # bmr.resolve_op_minutes.)
        op_minutes = bmr.resolve_op_minutes(stage.op_minutes)

        best_equipment, best_start, best_end = None, None, None
        for equip_id in stage.equipment_ids:
            equip = session.get(Equipment, equip_id)
            if equip is None or equip.status != "Active":
                continue
            free_start = _earliest_free_slot(session, equip_id, ready_time, op_minutes)
            free_end = free_start + timedelta(minutes=op_minutes)
            if best_end is None or free_end < best_end:
                best_equipment, best_start, best_end = equip_id, free_start, free_end

        if best_equipment is None:
            raise SchedulingError(
                f"No active equipment available for stage '{stage.name}' "
                f"(configured IDs: {', '.join(stage.equipment_ids)})"
            )

        clean_end = best_end + timedelta(minutes=stage.clean_minutes)
        # A freshly-drawn actual temperature per batch (within the recipe's standard
        # range, or +/-1-2 degrees of an exact value) — the standard/target value itself
        # (stage.temperature) is never touched, only read from. A manually-set Actual
        # Temperature reference on the recipe (stage.actual_temperature) is preferred
        # as the base when present.
        actual_temperature = bmr.compute_actual_temperature(stage.temperature, stage.actual_temperature)
        session.add(Allocation(
            batch_id=batch_id, equipment_id=best_equipment, stage_name=stage.name,
            stage_seq=seq, op_start=best_start, op_end=best_end, clean_end=clean_end,
            temperature=actual_temperature,
        ))
        stage_results.append(StageAllocationResult(
            stage.name, best_equipment, best_start, best_end, clean_end, actual_temperature, seq,
        ))
        ready_time = best_end

    return stage_results


def schedule_order(session: Session, product_code: str, quantity_kg: float,
                    requested_start: datetime, priority: int = 0,
                    planner: str | None = None, batch_label: str | None = None) -> OrderResult:
    product = session.get(Product, product_code)
    if product is None or not product.active:
        raise SchedulingError(f"Unknown or inactive product '{product_code}'")

    stages = recipes.read_recipe(product.recipe_sheet)
    if not stages:
        raise SchedulingError(f"Product '{product_code}' has no recipe stages defined yet")
    if product.batch_size_kg <= 0:
        raise SchedulingError(
            f"Product '{product_code}' has no batch size set yet — set it on the Products page first"
        )

    num_batches = math.ceil(quantity_kg / product.batch_size_kg)

    order = Order(product_code=product_code, quantity_kg=quantity_kg,
                   requested_start=requested_start, priority=priority, planner=planner,
                   batch_label=batch_label)
    session.add(order)
    session.flush()

    batch_results: list[BatchResult] = []
    for batch_num in range(1, num_batches + 1):
        batch = Batch(order_id=order.id, batch_number=batch_num, product_code=product_code,
                       label=_batch_label(batch_label, batch_num, num_batches))
        session.add(batch)
        session.flush()

        stage_results = _schedule_stages(session, batch.id, product.recipe_sheet, stages, requested_start)
        ecr.generate_cleaning_steps_for_batch(session, batch.id)
        batch_results.append(BatchResult(batch_num, batch.id, batch.label, stage_results))

    session.add(AuditLog(
        action="SCHEDULE_ORDER",
        details=(f"Order #{order.id}: {num_batches} batch(es) of {product_code} "
                  f"({quantity_kg} kg) requested by {planner or 'unknown'}"
                  + (f", batch no. {batch_label}" if batch_label else "")),
    ))
    session.commit()

    return OrderResult(order.id, product_code, quantity_kg, num_batches, batch_results)


def reschedule_batch(session: Session, batch_id: int, new_start: datetime,
                      actor: str | None = None) -> BatchResult:
    """Drops one batch's existing allocations and re-schedules it fresh from
    `new_start`, around every *other* booking already on the books. Doesn't
    touch any other batch — this only ever moves the one batch you ask for."""
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise SchedulingError(f"Batch {batch_id} not found")

    product = session.get(Product, batch.product_code)
    if product is None:
        raise SchedulingError(f"Product '{batch.product_code}' not found")

    stages = recipes.read_recipe(product.recipe_sheet)
    if not stages:
        raise SchedulingError(f"Product '{batch.product_code}' has no recipe stages defined")

    session.query(Allocation).filter(Allocation.batch_id == batch.id).delete()
    session.query(CleaningStep).filter(CleaningStep.batch_id == batch.id).delete()
    session.flush()

    stage_results = _schedule_stages(session, batch.id, product.recipe_sheet, stages, new_start)
    ecr.generate_cleaning_steps_for_batch(session, batch.id)

    session.add(AuditLog(
        action="RESCHEDULE_BATCH",
        details=(f"{actor or 'unknown'} rescheduled batch {batch.id} "
                  f"(order #{batch.order_id}, {batch.product_code} batch {batch.batch_number}) "
                  f"to start {new_start:%d/%m/%Y %H:%M}"),
    ))
    session.commit()

    return BatchResult(batch.batch_number, batch.id, batch.label, stage_results)


def delete_batch(session: Session, batch_id: int, actor: str | None = None) -> None:
    """Removes one batch and its allocations, freeing up whatever equipment
    time it was holding. If it was the only batch on its order, the order is
    removed too; otherwise sibling batches are untouched."""
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise SchedulingError(f"Batch {batch_id} not found")

    order = session.get(Order, batch.order_id)
    description = f"batch {batch.id} (order #{batch.order_id}, {batch.product_code} batch {batch.batch_number})"

    session.query(Allocation).filter(Allocation.batch_id == batch.id).delete()
    session.query(CleaningStep).filter(CleaningStep.batch_id == batch.id).delete()
    session.delete(batch)
    session.flush()

    remaining_siblings = session.query(Batch).filter(Batch.order_id == order.id).count()
    if remaining_siblings == 0:
        session.delete(order)

    session.add(AuditLog(action="DELETE_BATCH", details=f"{actor or 'unknown'} deleted {description}"))
    session.commit()


def set_batch_paused(session: Session, batch_id: int, paused: bool, actor: str | None = None) -> None:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise SchedulingError(f"Batch {batch_id} not found")
    batch.status = "Paused" if paused else "Scheduled"
    session.add(AuditLog(
        action="PAUSE_BATCH" if paused else "RESUME_BATCH",
        details=f"{actor or 'unknown'} {'paused' if paused else 'resumed'} batch {batch.id} "
                f"(order #{batch.order_id}, {batch.product_code} batch {batch.batch_number})",
    ))
    session.commit()


def equipment_status_at(session: Session, equipment: Equipment, as_of: datetime) -> dict:
    """Current occupancy of one piece of equipment at a given moment."""
    if equipment.status != "Active":
        return {"status": equipment.status, "allocation": None}

    alloc = (
        session.query(Allocation)
        .filter(Allocation.equipment_id == equipment.id,
                Allocation.op_start <= as_of, Allocation.clean_end > as_of)
        .order_by(Allocation.op_start.desc())
        .first()
    )
    if alloc is None:
        return {"status": "Free", "allocation": None}
    if as_of < alloc.op_end:
        return {"status": "Running", "allocation": alloc}
    return {"status": "Cleaning", "allocation": alloc}
