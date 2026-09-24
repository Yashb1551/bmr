"""Admin: manage already-scheduled batches — reschedule, pause/resume, or delete."""
from datetime import date, datetime, time

import streamlit as st

from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import Allocation, Batch, Order, Product, User, filter_products_for_user, product_display_names
from batch_planner import scheduler

st.set_page_config(page_title="Batches", page_icon="🗂️", layout="wide")
current_user = require_login(min_role=["Admin", "Manager"])
is_admin = current_user["role"] == "Admin"

st.title("🗂️ Batches")
st.caption("Reschedule, pause, or delete a batch that's already on the books. Rescheduling or "
           "deleting only ever touches the one batch you pick — sibling batches in the same "
           "order, and every other order, are left exactly as they are.")
if not is_admin:
    st.caption("Signed in as Manager: only batches for your assigned products are shown here.")

with SessionLocal() as session:
    batches = (
        session.query(Batch)
        .join(Order, Batch.order_id == Order.id)
        .order_by(Order.id.desc(), Batch.batch_number)
        .all()
    )
    if not is_admin:
        account = session.get(User, current_user["username"])
        allowed = set(filter_products_for_user(
            account.role, account.allowed_products, [b.product_code for b in batches]
        ))
        batches = [b for b in batches if b.product_code in allowed]
    product_labels_by_code = product_display_names(session)
    # One batch produces one batch-size worth of product, so that is this
    # batch's output quantity (an order's total is split into
    # ceil(quantity / batch_size) of them).
    batch_size_by_code = {p.code: p.batch_size_kg for p in session.query(Product).all()}
    rows = []
    for b in batches:
        allocations = sorted(b.allocations, key=lambda a: a.op_start)
        rows.append({
            "batch_id": b.id,
            "Order": b.order_id,
            "Product": product_labels_by_code.get(b.product_code, b.product_code),
            "Batch #": b.batch_number,
            "Batch No.": b.label or "",
            "Output Qty (kg)": batch_size_by_code.get(b.product_code),
            "Status": b.status,
            "Planner": b.order.planner or "",
            "Start": allocations[0].op_start if allocations else None,
            "Completion": allocations[-1].op_end if allocations else None,
            "Operations": len(allocations),
        })

# Earliest start first, so the table reads as a production calendar. Batches
# with no bookings at all sort last rather than crashing the comparison.
rows.sort(key=lambda r: (r["Start"] is None, r["Start"] or datetime.max))

if not rows:
    if not is_admin:
        st.info("No products have been assigned to your account yet, or none have batches scheduled. "
                "Ask an Admin to assign products on the **Users** page (Product Access tab).")
    else:
        st.info("No batches scheduled yet. Go to the Scheduler page to schedule one.")
    st.stop()

st.subheader("Scheduled products")
total_output = sum(r["Output Qty (kg)"] or 0 for r in rows)
st.caption(f"{len(rows)} batch(es), earliest start first — {total_output:g} kg of total output.")
st.dataframe(
    [{k: v for k, v in r.items() if k != "batch_id"} | {
        "Output Qty (kg)": f"{r['Output Qty (kg)']:g}" if r["Output Qty (kg)"] else "",
        "Start": r["Start"].strftime("%d/%m/%Y %H:%M") if r["Start"] else "",
        "Completion": r["Completion"].strftime("%d/%m/%Y %H:%M") if r["Completion"] else "",
    } for r in rows],
    hide_index=True, width='stretch',
)

st.divider()

batch_options = {
    f"Order #{r['Order']} · {r['Product']} · Batch {r['Batch #']}"
    + (f" ({r['Batch No.']})" if r["Batch No."] else "") + f" — {r['Status']}": r["batch_id"]
    for r in rows
}
selected_label = st.selectbox("Select a batch to manage", list(batch_options.keys()))
selected_id = batch_options[selected_label]

with SessionLocal() as session:
    batch = session.get(Batch, selected_id)
    allocations = sorted(batch.allocations, key=lambda a: a.op_start)
    product = session.get(Product, batch.product_code)
    product_label = f"{product.name} ({batch.product_code})" if product else batch.product_code
    st.markdown(f"**{product_label}** batch {batch.batch_number} "
                f"(order #{batch.order_id}) — status: **{batch.status}**")
    st.dataframe(
        [{"Op. No.": a.stage_seq, "Operation": a.stage_name, "Equipment": a.equipment_id,
          "Start": a.op_start.strftime("%d/%m/%Y %H:%M"), "End": a.op_end.strftime("%d/%m/%Y %H:%M")}
         for a in allocations],
        hide_index=True, width='stretch', height=250,
    )

tab_reschedule, tab_pause, tab_delete = st.tabs(["Reschedule", "Pause / Resume", "Delete"])

with tab_reschedule:
    st.caption("Drops this batch's current bookings and re-runs it from a new start time, "
               "slotting around whatever else is already scheduled — exactly like a fresh "
               "order, but for this one batch only.")
    col1, col2 = st.columns(2)
    with col1:
        new_date = st.date_input("New earliest start date", value=date.today(), format="DD/MM/YYYY")
    with col2:
        new_time = st.time_input("New earliest start time", value=time(8, 0))
    if st.button("Reschedule this batch", type="primary"):
        new_start = datetime.combine(new_date, new_time)
        with SessionLocal() as session:
            try:
                result = scheduler.reschedule_batch(session, selected_id, new_start,
                                                      actor=current_user["username"])
            except scheduler.SchedulingError as exc:
                st.error(f"Could not reschedule: {exc}")
            else:
                st.success(f"Rescheduled — now completes {result.completion:%d/%m/%Y %H:%M}.")
                st.rerun()

with tab_pause:
    st.caption("Pausing is a status flag for tracking (e.g. on hold for QC) — it does not free "
               "or move this batch's already-booked equipment time. Reschedule or delete it if "
               "you need that equipment released.")
    if batch.status == "Paused":
        if st.button("Resume batch"):
            with SessionLocal() as session:
                scheduler.set_batch_paused(session, selected_id, False, actor=current_user["username"])
            st.success("Batch resumed.")
            st.rerun()
    else:
        if st.button("Pause batch"):
            with SessionLocal() as session:
                scheduler.set_batch_paused(session, selected_id, True, actor=current_user["username"])
            st.success("Batch paused.")
            st.rerun()

with tab_delete:
    st.caption("Permanently removes this batch and its equipment bookings. If it's the only "
               "batch on its order, the order is removed too.")
    confirm = st.checkbox(f"Confirm permanent delete of this batch")
    if st.button("Delete batch permanently", disabled=not confirm):
        with SessionLocal() as session:
            scheduler.delete_batch(session, selected_id, actor=current_user["username"])
        st.success("Batch deleted.")
        st.rerun()
