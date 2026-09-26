"""SQLAlchemy ORM models for the plant's equipment registry, products, orders,
batches and the schedule itself (Allocation = one stage of one batch sitting
on one piece of equipment for a time window)."""
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "R-01"
    category: Mapped[str] = mapped_column(String)  # Reactor / Dryer / Multi Mill / Jet Mill / Blender / Crystallizer / Centrifuge
    subtype: Mapped[str | None] = mapped_column(String, nullable=True)  # GLR / SSR / None
    capacity_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    area: Mapped[str] = mapped_column(String)  # Main Reaction Area / Intermediate Area / Finishing Line 1 ...
    status: Mapped[str] = mapped_column(String, default="Active")  # Active / Down / Retired
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


def equipment_block(equipment_id: str) -> str:
    """Plant block (e.g. "B2") derived from an ID like "MA/B2/E/026" —
    falls back to "Other" for IDs that don't follow that convention."""
    for part in equipment_id.split("/"):
        if 2 <= len(part) <= 3 and part[:1].upper() == "B" and part[1:].isdigit():
            return part.upper()
    return "Other"


class Product(Base):
    __tablename__ = "products"

    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    batch_size_kg: Mapped[float] = mapped_column(Float)
    recipe_sheet: Mapped[str] = mapped_column(String)  # -> RecipeSheet.name
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RecipeSheet(Base):
    """One product recipe (was a sheet in recipes.xlsx). Kept as its own row
    so an empty, not-yet-filled recipe still 'exists' for the BMR Master page."""
    __tablename__ = "recipe_sheets"

    name: Mapped[str] = mapped_column(String, primary_key=True)


class RecipeStage(Base):
    """One operation row of one recipe, in run order (was one column in a
    recipes.xlsx sheet)."""
    __tablename__ = "recipe_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sheet: Mapped[str] = mapped_column(String, index=True)  # -> RecipeSheet.name
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    op_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    clean_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    equipment_ids: Mapped[str] = mapped_column(String, default="")  # comma-separated IDs
    temperature: Mapped[str] = mapped_column(String, default="")
    actual_temperature: Mapped[str] = mapped_column(String, default="")


class EcrTemplate(Base):
    """One equipment category's cleaning procedure (was a sheet in
    ecr_templates.xlsx). `key` is category, or "Dryer-<subtype>"."""
    __tablename__ = "ecr_template_sheets"

    key: Mapped[str] = mapped_column(String, primary_key=True)


class EcrTemplateStep(Base):
    """One cleaning step of one template, in order."""
    __tablename__ = "ecr_template_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_key: Mapped[str] = mapped_column(String, index=True)  # -> EcrTemplate.key
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    op_minutes: Mapped[float] = mapped_column(Float, default=0.0)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"))
    quantity_kg: Mapped[float] = mapped_column(Float)
    requested_start: Mapped[datetime] = mapped_column(DateTime)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    planner: Mapped[str | None] = mapped_column(String, nullable=True)
    batch_label: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "KETO/2026/07/001"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    batches: Mapped[list["Batch"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Indexed foreign keys throughout: the scheduler looks a batch's rows up by
    # these on every stage it places. SQLite scanned them fine in-process, but
    # against a network database (Supabase) the sequential scans dominate once
    # the schedule has any real history behind it.
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    batch_number: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="Scheduled")
    label: Mapped[str | None] = mapped_column(String, nullable=True)  # user-entered batch no., suffixed if split

    order: Mapped["Order"] = relationship(back_populates="batches")
    allocations: Mapped[list["Allocation"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    cleaning_steps: Mapped[list["CleaningStep"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class Allocation(Base):
    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    stage_name: Mapped[str] = mapped_column(String)
    stage_seq: Mapped[int] = mapped_column(Integer)
    temperature: Mapped[str | None] = mapped_column(String, nullable=True)
    op_start: Mapped[datetime] = mapped_column(DateTime)
    op_end: Mapped[datetime] = mapped_column(DateTime)
    clean_end: Mapped[datetime] = mapped_column(DateTime)  # equipment is unbookable until this

    batch: Mapped["Batch"] = relationship(back_populates="allocations")


class CleaningStep(Base):
    """One step of one piece of equipment's Equipment Cleaning Record (ECR)
    for one batch — generated once at schedule time (like Allocation) from
    that equipment category's imported ECR template, starting the moment
    that equipment's *last* use in the batch ends. Purely a documentation
    trail: it doesn't affect equipment availability, which is still governed
    by Allocation.clean_end."""
    __tablename__ = "cleaning_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step_name: Mapped[str] = mapped_column(String)
    step_start: Mapped[datetime] = mapped_column(DateTime)
    step_end: Mapped[datetime] = mapped_column(DateTime)
    last_operation: Mapped[str] = mapped_column(String)  # the batch operation this equipment was doing right before cleaning
    last_op_no: Mapped[int | None] = mapped_column(Integer, nullable=True)  # that operation's BMR Op. No. (stage_seq); None for rows generated before this field existed

    batch: Mapped["Batch"] = relationship(back_populates="cleaning_steps")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    action: Mapped[str] = mapped_column(String)
    details: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="Planner")  # "Admin" or "Planner"
    active: Mapped[bool] = mapped_column(Boolean, default=True)


def product_display_names(session) -> dict[str, str]:
    """{product_code: "Name (Code)"} for every product — used everywhere a
    batch/order/allocation is shown, so it reads by product name instead of
    just its code. Falls back to the bare code for a product_code that no
    longer has a matching Product row (shouldn't normally happen — Products
    with order history can't be deleted — but kept defensive)."""
    return {p.code: f"{p.name} ({p.code})" for p in session.query(Product).all()}
