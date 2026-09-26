"""Scheduler: schedule a new batch, look up an existing batch's schedule, and
check equipment cleaning status. Run with `streamlit run Scheduler.py` from
the Main Codes/ folder."""
from datetime import date, datetime, time

import pandas as pd
import plotly.express as px
import streamlit as st

from batch_planner import ecr, pdf_export, scheduler, style
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import Batch, Order, Product, product_display_names

st.set_page_config(page_title="Batch Planner", page_icon="🧪", layout="wide")
user = require_login()

st.title("🧪 Batch Production Scheduler")
st.caption("Pharmaceutical API Manufacturing Unit — new batches are slotted around "
           "whatever is already scheduled; nothing existing is ever overwritten.")


def render_batch_schedule(session, batch_id: int, key_prefix: str = "") -> None:
    """One batch's full output: production timeline, BMR-style operation
    table, equipment/cleaning detail, and — below all of that, in its own
    section — the Equipment Cleaning Record (ECR) log for every piece of
    equipment the batch used. Shared by both the schedule-a-batch result and
    the view-an-existing-batch section below, so they always look the same.
    `key_prefix` must be unique per call site — the same batch can legitimately
    render twice in one script run (e.g. right after being scheduled, it's
    also the top entry in the "view an existing batch" list further down)."""
    batch = session.get(Batch, batch_id)
    if batch is None:
        st.error("This batch no longer exists.")
        return
    allocations = sorted(batch.allocations, key=lambda a: a.op_start)
    if not allocations:
        st.info("This batch has no scheduled operations.")
        return

    completion = allocations[-1].op_end
    label_bit = f" (Batch No. {batch.label})" if batch.label else ""
    product = session.get(Product, batch.product_code)
    product_label = f"{product.name} ({batch.product_code})" if product else batch.product_code
    st.markdown(f"**{product_label} — Batch {batch.batch_number}{label_bit}** "
                f"— completes {completion:%d/%m/%Y %H:%M}")

    timeline_df = pd.DataFrame([{
        "Equipment": a.equipment_id, "Operation": a.stage_name,
        "Start": a.op_start, "Finish": a.clean_end,
    } for a in allocations])
    fig = px.timeline(timeline_df, x_start="Start", x_end="Finish", y="Equipment",
                       hover_data=["Operation"])
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(tickformat="%d/%m/%Y %H:%M")
    fig.update_layout(height=max(300, 40 * timeline_df["Equipment"].nunique()),
                       title=f"Timeline — Batch {batch.batch_number}", showlegend=False)
    st.plotly_chart(fig, width='stretch', key=f"timeline_{key_prefix}_{batch_id}")

    bmr_df = pd.DataFrame([{
        "Op. No.": a.stage_seq,
        "Operation": a.stage_name,
        "Date": a.op_start.strftime("%d/%m/%Y"),
        "Initial Time": a.op_start.strftime("%H:%M"),
        "Final Time": a.op_end.strftime("%H:%M"),
        "Temperature Actual": a.temperature,
    } for a in allocations])
    st.dataframe(style.highlight_qc_sample(bmr_df), hide_index=True, width='stretch')

    file_stub = pdf_export.safe_filename(batch.label or f"{batch.product_code}-batch-{batch.batch_number}")
    meta_lines = [
        f"Product: {product_label}",
        f"Batch number: {batch.batch_number}" + (f"   Batch No.: {batch.label}" if batch.label else ""),
        f"Completes: {completion:%d/%m/%Y %H:%M}",
    ]
    bmr_pdf = pdf_export.build_tables_pdf(
        f"Batch Manufacturing Record - {product_label}", meta_lines,
        [pdf_export.PdfSection("Operations", bmr_df)],
    )
    st.download_button(
        "⬇ Download BMR (PDF)", bmr_pdf, file_name=f"BMR_{file_stub}.pdf",
        mime="application/pdf", key=f"bmr_pdf_{key_prefix}_{batch_id}",
    )

    with st.expander("Equipment & cleaning detail"):
        detail_df = pd.DataFrame([{
            "Op. No.": a.stage_seq,
            "Operation": a.stage_name,
            "Equipment": a.equipment_id,
            "Temperature Actual": a.temperature,
            "Start": a.op_start.strftime("%d/%m/%Y %H:%M"),
            "Operation Ends": a.op_end.strftime("%d/%m/%Y %H:%M"),
            "Equipment Free After Cleaning": a.clean_end.strftime("%d/%m/%Y %H:%M"),
        } for a in allocations])
        st.dataframe(style.highlight_qc_sample(detail_df), hide_index=True, width='stretch')

    st.markdown("###### Equipment Cleaning Record (ECR) log")
    ecr_entries = ecr.ecr_log_for_batch(session, batch_id)
    if not ecr_entries:
        st.caption("No cleaning steps recorded for this batch yet.")
    ecr_sections = []
    for entry in ecr_entries:
        note = ("" if entry.template_found else
                " _(no imported ECR template for this equipment category yet — "
                "generic single-step cleaning shown; import one on the Equipment page.)_")
        op_no_bit = f"Op. No. {entry.last_op_no}" if entry.last_op_no is not None else "an earlier operation"
        st.markdown(
            f"**{entry.equipment_id}** ({entry.category}) — cleaning after **{op_no_bit}**, "
            f"held clean until **{entry.held_clean_until:%d/%m/%Y %H:%M}**{note}"
        )
        if not entry.steps:
            st.caption("No cleaning duration available for this equipment yet.")
            continue
        ecr_df = pd.DataFrame([{
            "Op. No.": s.seq,
            "Operation": s.text,
            "Date": s.op_start.strftime("%d/%m/%Y"),
            "Initial Time": s.op_start.strftime("%H:%M"),
            "Final Time": s.op_end.strftime("%H:%M"),
        } for s in entry.steps])
        st.dataframe(style.highlight_qc_sample(ecr_df), hide_index=True, width='stretch')
        ecr_sections.append(pdf_export.PdfSection(
            f"{entry.equipment_id} ({entry.category}) - cleaning after {op_no_bit}, "
            f"held clean until {entry.held_clean_until:%d/%m/%Y %H:%M}", ecr_df,
        ))

    if ecr_sections:
        ecr_pdf = pdf_export.build_tables_pdf(
            f"Equipment Cleaning Record - {product_label}", meta_lines, ecr_sections,
        )
        st.download_button(
            "⬇ Download ECR (PDF)", ecr_pdf, file_name=f"ECR_{file_stub}.pdf",
            mime="application/pdf", key=f"ecr_pdf_{key_prefix}_{batch_id}",
        )


with SessionLocal() as session:
    # Every role can schedule any active product — a Manager's product
    # assignment scopes what they can manage afterwards (Batches page), not
    # what they are allowed to start.
    products = session.query(Product).filter(Product.active == True).order_by(Product.name).all()

st.subheader("Schedule a New Batch")

if not products:
    st.warning("No active products yet. Go to **BMR Master** (left sidebar) to add one.")
    st.stop()

product_labels = {f"{p.name} ({p.code})": p.code for p in products}

with st.form("schedule_form"):
    col1, col2 = st.columns(2)
    with col1:
        label = st.selectbox("Product", list(product_labels.keys()))
        quantity_kg = st.number_input("Quantity requested (kg)", min_value=0.1, value=100.0, step=10.0)
        priority = st.selectbox("Priority", options=[0, 1, 2],
                                 format_func=lambda x: {0: "Normal", 1: "High", 2: "Urgent"}[x])
    with col2:
        req_date = st.date_input("Earliest start date", value=date.today(), format="DD/MM/YYYY")
        req_time = st.time_input("Earliest start time", value=time(8, 0))
        batch_label_input = st.text_input(
            "Batch number (optional)", placeholder="e.g. UT/A028/07",
            help="Recorded on the order and shown on the filled BMR. If the quantity splits into "
                 "several batches, each gets this number suffixed with -1, -2, ...",
        )
    submitted = st.form_submit_button("Schedule batch(es)", type="primary")

if submitted:
    product_code = product_labels[label]
    requested_start = datetime.combine(req_date, req_time)
    with SessionLocal() as session:
        product = session.get(Product, product_code)
        try:
            result = scheduler.schedule_order(
                session, product_code, quantity_kg, requested_start,
                priority=priority, planner=user["display_name"],
                batch_label=batch_label_input.strip() or None,
            )
        except scheduler.SchedulingError as exc:
            st.error(f"Could not schedule this order: {exc}")
        else:
            st.success(
                f"Order #{result.order_id} scheduled: **{result.num_batches} batch(es)** of "
                f"{product.name} ({quantity_kg} kg, batch size {product.batch_size_kg} kg each). "
                f"Estimated completion: **{result.completion:%d/%m/%Y %H:%M}**."
            )
            for b in result.batches:
                render_batch_schedule(session, b.batch_id, key_prefix="new")
            st.info("See **Equipment Map** for live occupancy or **Gantt Timeline** for the full schedule.")

st.divider()
st.subheader("View an Existing Batch's Schedule")
st.caption("Look up any already-scheduled batch's full output — timeline, BMR-style operation "
           "table, and ECR cleaning log — exactly as shown right after scheduling.")

with SessionLocal() as session:
    view_batches = (
        session.query(Batch)
        .join(Order, Batch.order_id == Order.id)
        .order_by(Order.id.desc(), Batch.batch_number)
        .all()
    )
    product_labels_by_code = product_display_names(session)
    view_rows = []
    for b in view_batches:
        allocs = sorted(b.allocations, key=lambda a: a.op_start)
        view_rows.append({
            "batch_id": b.id, "Order": b.order_id,
            "Product": product_labels_by_code.get(b.product_code, b.product_code),
            "Batch #": b.batch_number, "Batch No.": b.label or "", "Status": b.status,
            "Start": allocs[0].op_start if allocs else None,
        })

if not view_rows:
    st.info("No batches scheduled yet.")
else:
    view_options = {
        f"Order #{r['Order']} · {r['Product']} · Batch {r['Batch #']}"
        + (f" ({r['Batch No.']})" if r["Batch No."] else "") + f" — {r['Status']}": r["batch_id"]
        for r in view_rows
    }
    view_selected_label = st.selectbox("Select a batch", list(view_options.keys()), key="view_batch_select")
    view_selected_id = view_options[view_selected_label]
    with SessionLocal() as session:
        render_batch_schedule(session, view_selected_id, key_prefix="view")

st.divider()
st.subheader("Equipment Cleaning Schedule")
st.caption("Once cleaned, equipment is considered held clean for "
           f"**{ecr.CLEAN_HOLD_HOURS} hours**; past that it needs cleaning again before its next use.")

with SessionLocal() as session:
    cleaning_rows = ecr.equipment_cleaning_schedule(session, datetime.now())

cleaning_df = pd.DataFrame([{
    "Equipment": r.equipment_id,
    "Category": r.category,
    "Area": r.area,
    "Last Batch": r.last_batch_label or "",
    "Cleaning Ended": r.cleaning_end.strftime("%d/%m/%Y %H:%M") if r.cleaning_end else "",
    "Held Clean Until": r.held_clean_until.strftime("%d/%m/%Y %H:%M") if r.held_clean_until else "",
    "Status": r.status,
    "Detail": r.hours_info,
    "Next Scheduled Use": r.next_use_start.strftime("%d/%m/%Y %H:%M") if r.next_use_start else "",
    "⚠ Needs re-clean before next use": "Yes" if r.next_use_flag else "",
} for r in cleaning_rows])

status_colors = {
    "Clean": "background-color: #c6efce; color: #006100",
    "Needs Cleaning": "background-color: #ffc7ce; color: #9c0006",
    "Running": "background-color: #fff2cc; color: #7f6000",
    "Cleaning": "background-color: #fff2cc; color: #7f6000",
    "Never Used": "",
}
st.dataframe(style.style_by_value(cleaning_df, "Status", status_colors), hide_index=True, width='stretch')
