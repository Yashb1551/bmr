"""Admin + Manager: the master Equipment Cleaning Record (ECR) template for
each equipment category — imported once from a Word document (Equipment
page, Admin only), editable here afterward by Admin or Manager. Once a
category's steps are edited and saved, that edited version is what every
future batch's ECR log is generated from — re-importing the original
document won't silently overwrite it (see the Equipment page's Import ECR
Templates tab)."""
import pandas as pd
import streamlit as st

from batch_planner import ecr_templates
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import AuditLog

st.set_page_config(page_title="ECR Master", page_icon="🧽", layout="wide")
current_user = require_login(min_role=["Admin", "Manager", "Temp Editor"])

st.title("🧽 ECR Master")
st.caption("One cleaning procedure per equipment category, used to generate every batch's ECR log "
           "on the Scheduler page. Edit a category's steps here and Save — that becomes the master "
           "for every future batch, exactly like editing a product's recipe on BMR Master.")


def _pretty_label(key: str) -> str:
    if "-" in key:
        base, sub = key.split("-", 1)
        return f"{base} ({sub})"
    return key


keys = ecr_templates.list_template_sheets()

if not keys:
    if current_user["role"] == "Admin":
        st.info("No ECR templates imported yet. Go to **Equipment** -> *Import ECR Templates* to add one.")
    else:
        st.info("No ECR templates imported yet. Ask an Admin to import one on the **Equipment** page.")
    st.stop()

summary_rows = []
for key in keys:
    steps = ecr_templates.read_template(key)
    summary_rows.append({
        "Category": _pretty_label(key),
        "Steps": len(steps),
        "Total Time (min)": round(sum(s.op_minutes for s in steps)),
    })
st.dataframe(pd.DataFrame(summary_rows), hide_index=True, width='stretch')

st.divider()

key_labels = {_pretty_label(k): k for k in keys}
selected_label = st.selectbox("Select ECR", list(key_labels.keys()), key="ecr_master_select")
selected_key = key_labels[selected_label]

if st.session_state.get("_ecr_master_last_selected") != selected_key:
    # Same nonce trick as the Products recipe editor — forces a fresh data_editor
    # grid when switching category so the old one's rows can't bleed through.
    st.session_state["_ecr_master_last_selected"] = selected_key
    st.session_state["_ecr_master_nonce"] = st.session_state.get("_ecr_master_nonce", 0) + 1
editor_key = f"ecr_editor_{selected_key}_{st.session_state.get('_ecr_master_nonce', 0)}"

steps = ecr_templates.read_template(selected_key)
df = pd.DataFrame([{"Operation": s.name, "Time (min)": s.op_minutes} for s in steps])
if df.empty:
    df = pd.DataFrame([{"Operation": "Step 1", "Time (min)": 0}])

st.markdown(f"#### {selected_label}")
st.caption("A step left at 0 (no time defined) is generated as 5 min ±2%, re-drawn for every "
           "batch's ECR — it's never treated as instantaneous.")
edited = st.data_editor(df, num_rows="dynamic", width='stretch', key=editor_key)

if st.button("Save ECR", type="primary"):
    new_steps = []
    for _, row in edited.iterrows():
        name = str(row["Operation"]).strip()
        if not name or name.lower() == "nan":
            continue
        minutes = pd.to_numeric(row["Time (min)"], errors="coerce")
        new_steps.append(ecr_templates.TemplateStep(
            name=name, op_minutes=round(float(minutes)) if pd.notna(minutes) else 0.0,
        ))
    ecr_templates.write_template(selected_key, new_steps)
    with SessionLocal() as session:
        session.add(AuditLog(
            action="UPDATE_ECR_TEMPLATE",
            details=f"{current_user['username']} updated ECR template '{selected_key}'",
        ))
        session.commit()
    st.success(f"Saved '{selected_label}' ({len(new_steps)} step(s)).")
    st.rerun()
