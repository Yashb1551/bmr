"""Full plant schedule as a Gantt-style timeline, equipment vs. time —
plus a focused view of a single batch's own timeline across equipment."""
import pandas as pd
import plotly.express as px
import streamlit as st

from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import Allocation, product_display_names

st.set_page_config(page_title="Gantt Timeline", page_icon="📊", layout="wide")
require_login()

st.title("📊 Gantt Timeline")

with SessionLocal() as session:
    allocations = session.query(Allocation).order_by(Allocation.op_start).all()
    product_labels_by_code = product_display_names(session)
    rows = []
    for a in allocations:
        batch = a.batch
        batch_bit = f" ({batch.label})" if batch.label else ""
        product_label = product_labels_by_code.get(batch.product_code, batch.product_code)
        rows.append({
            "Op. No.": a.stage_seq,
            "Equipment": a.equipment_id,
            "Operation": a.stage_name,
            "Temperature Actual": a.temperature or "",
            "Start": a.op_start,
            "Finish": a.clean_end,
            "Product": product_label,
            "Order": batch.order_id,
            "BatchNumber": batch.batch_number,
            "BatchLabel": f"Order #{batch.order_id} · {product_label} · Batch {batch.batch_number}{batch_bit}",
        })

if not rows:
    st.info("No batches scheduled yet. Go to the Scheduler page to schedule one.")
    st.stop()

df = pd.DataFrame(rows)

st.subheader("Focus on one batch")
batch_labels = ["All batches"] + sorted(
    df["BatchLabel"].unique(), key=lambda s: (int(s.split("Order #")[1].split(" ")[0]))
)
focus = st.selectbox("Select an order/batch to view its own timeline across equipment", batch_labels)

if focus != "All batches":
    focus_df = df[df["BatchLabel"] == focus].sort_values("Start")
    fig_focus = px.timeline(
        focus_df, x_start="Start", x_end="Finish", y="Equipment", color="Operation", text="Operation",
    )
    fig_focus.update_yaxes(autorange="reversed")
    fig_focus.update_xaxes(tickformat="%d/%m/%Y %H:%M")
    fig_focus.update_layout(height=max(300, 60 * focus_df["Equipment"].nunique()), title=focus)
    st.plotly_chart(fig_focus, width='stretch')
    focus_display = focus_df[["Op. No.", "Operation", "Equipment", "Temperature Actual", "Start", "Finish"]].copy()
    focus_display["Start"] = focus_display["Start"].dt.strftime("%d/%m/%Y %H:%M")
    focus_display["Finish"] = focus_display["Finish"].dt.strftime("%d/%m/%Y %H:%M")
    st.dataframe(
        focus_display.rename(columns={"Finish": "Equipment Free After"}),
        hide_index=True, width='stretch',
    )

st.divider()
st.subheader("Full plant schedule")

col1, col2 = st.columns(2)
with col1:
    products = sorted(df["Product"].unique())
    selected_products = st.multiselect("Filter by product", products, default=products)
with col2:
    equipments = sorted(df["Equipment"].unique())
    selected_equipment = st.multiselect("Filter by equipment", equipments, default=equipments)

filtered = df[df["Product"].isin(selected_products) & df["Equipment"].isin(selected_equipment)]

if filtered.empty:
    st.warning("No allocations match the current filters.")
    st.stop()

fig = px.timeline(
    filtered, x_start="Start", x_end="Finish", y="Equipment", color="Product",
    hover_data=["Operation", "BatchLabel"],
)
fig.update_yaxes(autorange="reversed")
fig.update_xaxes(tickformat="%d/%m/%Y %H:%M")
fig.update_layout(height=max(400, 30 * filtered["Equipment"].nunique()))
st.plotly_chart(fig, width='stretch')

st.subheader("Raw schedule")
raw_display = filtered.sort_values("Start").copy()
raw_display["Start"] = raw_display["Start"].dt.strftime("%d/%m/%Y %H:%M")
raw_display["Finish"] = raw_display["Finish"].dt.strftime("%d/%m/%Y %H:%M")
st.dataframe(raw_display, hide_index=True, width='stretch')
