"""Live occupancy map — every reactor/dryer/mill/blender, colored by current status.
Grouped by plant block (API B2 / API B3), then floor, then equipment category
(Reactors first)."""
from datetime import datetime

import streamlit as st

from batch_planner import scheduler
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import Equipment, equipment_block, product_display_names

st.set_page_config(page_title="Equipment Map", page_icon="🗺️", layout="wide")
require_login()

st.title("🗺️ Equipment Map")

col1, col2 = st.columns(2)
with col1:
    as_of_date = st.date_input("As of date", value=datetime.now().date(), format="DD/MM/YYYY")
with col2:
    as_of_time = st.time_input("As of time", value=datetime.now().time().replace(microsecond=0))
as_of = datetime.combine(as_of_date, as_of_time)

STATUS_COLORS = {
    "Free": "#1e8e3e",
    "Running": "#d93025",
    "Cleaning": "#f9ab00",
    "Down": "#5f6368",
    "Retired": "#202124",
}

BLOCK_ORDER = ["B2", "B3"]
FLOOR_ORDER = ["Ground Floor", "Second Floor", "Third Floor", "Fourth Floor"]


def _floor_key(area: str) -> int:
    return FLOOR_ORDER.index(area) if area in FLOOR_ORDER else len(FLOOR_ORDER)


def _category_key(category: str):
    return (0, "") if category == "Reactor" else (1, category)


def _render_tile(e: Equipment, info: dict, product_labels_by_code: dict[str, str]) -> None:
    color = STATUS_COLORS.get(info["status"], "#888888")
    alloc = info.get("allocation")
    detail = ""
    if alloc is not None:
        batch = alloc.batch
        product_label = product_labels_by_code.get(batch.product_code, batch.product_code)
        until = alloc.op_end if info["status"] == "Running" else alloc.clean_end
        detail = (f"<br><small>{product_label} · batch {batch.batch_number}"
                  f"<br>until {until:%H:%M}</small>")
    spec_bits = [e.category]
    if e.subtype:
        spec_bits.append(e.subtype)
    if e.capacity_l:
        spec_bits.append(f"{int(e.capacity_l)} L")
    st.markdown(
        f"""
        <div style="border-radius:8px;padding:10px;margin-bottom:10px;
                    background:{color};color:white;">
            <b>{e.id}</b> <span style="float:right">{info['status']}</span><br>
            <small>{' · '.join(spec_bits)}</small>
            {detail}
        </div>
        """,
        unsafe_allow_html=True,
    )


with SessionLocal() as session:
    equipment = session.query(Equipment).all()
    if not equipment:
        st.info("No equipment registered yet. Go to Equipment (left sidebar) to add some.")

    status_by_id = {e.id: scheduler.equipment_status_at(session, e, as_of) for e in equipment}
    product_labels_by_code = product_display_names(session)

    by_block: dict[str, list[Equipment]] = {b: [] for b in BLOCK_ORDER}
    for e in equipment:
        by_block.setdefault(equipment_block(e.id), []).append(e)

    for block in list(by_block.keys()):
        st.header(f"API {block}" if block in BLOCK_ORDER else block)
        items = by_block[block]
        if not items:
            st.caption("No equipment yet.")
            continue

        by_floor: dict[str, list[Equipment]] = {}
        for e in items:
            by_floor.setdefault(e.area, []).append(e)

        for floor in sorted(by_floor.keys(), key=_floor_key):
            st.subheader(floor)
            floor_items = sorted(by_floor[floor], key=lambda e: (_category_key(e.category), e.id))
            cols = st.columns(4)
            for i, e in enumerate(floor_items):
                with cols[i % 4]:
                    _render_tile(e, status_by_id[e.id], product_labels_by_code)

st.caption("Colors: green = Free, red = Running, amber = Cleaning/changeover, "
           "gray = Down for maintenance, black = Retired.")
