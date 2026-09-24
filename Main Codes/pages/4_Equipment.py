"""Admin: add, retire, or delete equipment."""
from pathlib import Path

import pandas as pd
import streamlit as st

from batch_planner import bmr, config, ecr_import, ecr_templates, equipment_import
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import Allocation, AuditLog, Equipment

st.set_page_config(page_title="Equipment", page_icon="⚙️", layout="wide")
current_user = require_login(min_role="Admin")

st.title("⚙️ Equipment")

CATEGORIES = ["Reactor", "Crystallizer", "Centrifuge", "Dryer", "Multi Mill", "Jet Mill", "Blender",
              "Weighing Balance", "Sparkler Filter", "Vacuum Pump", "Sifter", "Nutsche Filter", "Candle Filter"]
AREAS = ["Ground Floor", "Second Floor", "Third Floor", "Fourth Floor",
         "Intermediate Area", "Main Reaction Area", "Finishing Line 1", "Finishing Line 2",
         "Finishing Line 3", "Drying Section", "Milling Section", "Blending Section", "Utility", "Other"]

with SessionLocal() as session:
    equipment = session.query(Equipment).order_by(Equipment.category, Equipment.id).all()
    st.dataframe(
        [{"ID": e.id, "Category": e.category, "Subtype": e.subtype, "Capacity (L)": e.capacity_l,
          "Area": e.area, "Status": e.status, "Notes": e.notes} for e in equipment],
        hide_index=True, width='stretch',
    )

st.divider()
tab_import, tab_ecr, tab_add, tab_manage = st.tabs(
    ["Import Equipment List", "Import ECR Templates", "Add Equipment", "Edit / Retire / Delete"]
)

with tab_import:
    st.caption(
        f"Drop `.doc` or `.docx` master equipment lists into `Database/Equipment List` and they'll "
        "appear here. Reads name, capacity, material of construction, real equipment ID, and floor "
        "(from the document's own floor-section headers) automatically."
    )
    eq_files = bmr.scan_folder(config.EQUIPMENT_LIST_FOLDER)
    if not eq_files:
        st.info(f"No equipment list files found in `{config.EQUIPMENT_LIST_FOLDER}` yet.")
    else:
        for f in eq_files:
            rel = f.relative_to(config.EQUIPMENT_LIST_FOLDER)
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"📄 **{rel}**")
            with col2:
                if st.button("Preview", key=f"eq_preview_{rel}"):
                    st.session_state["eq_import_target"] = str(f)
                    st.session_state.pop("eq_import_parsed", None)
                    st.rerun()

    eq_target = st.session_state.get("eq_import_target")
    if eq_target:
        st.divider()
        st.markdown(f"#### Importing: {Path(eq_target).name}")
        if "eq_import_parsed" not in st.session_state:
            try:
                st.session_state["eq_import_parsed"] = equipment_import.parse_equipment_document(eq_target)
            except bmr.BMRParseError as exc:
                st.error(str(exc))
                st.session_state["eq_import_parsed"] = None

        eq_parsed = st.session_state.get("eq_import_parsed")
        if eq_parsed is not None:
            with SessionLocal() as session:
                existing_ids = {e.id for e in session.query(Equipment).all()}
            new_items = [p for p in eq_parsed if p.equipment_id not in existing_ids]
            update_items = [p for p in eq_parsed if p.equipment_id in existing_ids]
            st.write(f"**{len(eq_parsed)} equipment items** parsed — "
                     f"{len(new_items)} new, {len(update_items)} already exist (will be updated).")
            st.dataframe(
                pd.DataFrame([{
                    "ID": p.equipment_id, "Name": p.name, "Category": p.category, "Subtype": p.subtype,
                    "Capacity": p.capacity_text, "MOC": p.moc, "Floor": p.area,
                    "Status": "Update" if p.equipment_id in existing_ids else "New",
                } for p in eq_parsed]),
                hide_index=True, width='stretch', height=300,
            )

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("Confirm Import", type="primary"):
                    with SessionLocal() as session:
                        for p in eq_parsed:
                            existing = session.get(Equipment, p.equipment_id)
                            notes = f"{p.name} | Capacity: {p.capacity_text} | MOC: {p.moc}"
                            if existing is None:
                                session.add(Equipment(
                                    id=p.equipment_id, category=p.category, subtype=p.subtype,
                                    capacity_l=p.capacity_l, area=p.area, status="Active", notes=notes,
                                ))
                            else:
                                existing.category, existing.subtype = p.category, p.subtype
                                existing.capacity_l, existing.area, existing.notes = p.capacity_l, p.area, notes
                        session.add(AuditLog(
                            action="IMPORT_EQUIPMENT_LIST",
                            details=(f"{current_user['username']} imported {Path(eq_target).name} "
                                      f"({len(new_items)} new, {len(update_items)} updated)"),
                        ))
                        session.commit()
                    st.session_state.pop("eq_import_target", None)
                    st.session_state.pop("eq_import_parsed", None)
                    st.success(f"Imported {len(eq_parsed)} equipment item(s).")
                    st.rerun()
            with btn_col2:
                if st.button("Cancel", key="eq_cancel"):
                    st.session_state.pop("eq_import_target", None)
                    st.session_state.pop("eq_import_parsed", None)
                    st.rerun()

with tab_ecr:
    st.caption(
        "Drop `.doc` or `.docx` cleaning-procedure documents into `Database/ECR` and they'll "
        "appear here — one per equipment **category** (e.g. Reactor, Blender, Centrifuge), read "
        "the same way a BMR is: the equipment category comes from the document's own 'Name of "
        "Equipment' header field, and every cleaning step's duration is computed from its "
        "instruction text using the same rules as BMR operations. These templates drive the ECR "
        "log shown on the Scheduler page for every piece of equipment used in a batch."
    )
    ecr_files = bmr.scan_folder(config.ECR_FOLDER)
    if not ecr_files:
        st.info(f"No ECR files found in `{config.ECR_FOLDER}` yet.")
    else:
        for f in ecr_files:
            rel = f.relative_to(config.ECR_FOLDER)
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"📄 **{rel}**")
            with col2:
                if st.button("Preview", key=f"ecr_preview_{rel}"):
                    st.session_state["ecr_import_target"] = str(f)
                    st.session_state.pop("ecr_import_parsed", None)
                    st.rerun()

    ecr_target = st.session_state.get("ecr_import_target")
    if ecr_target:
        st.divider()
        st.markdown(f"#### Importing: {Path(ecr_target).name}")
        if "ecr_import_parsed" not in st.session_state:
            try:
                st.session_state["ecr_import_parsed"] = ecr_import.parse_ecr_document(ecr_target)
            except bmr.BMRParseError as exc:
                st.error(str(exc))
                st.session_state["ecr_import_parsed"] = None

        ecr_parsed = st.session_state.get("ecr_import_parsed")
        if ecr_parsed is not None:
            key = ecr_templates.cleaning_key_for(ecr_parsed.category, ecr_parsed.subtype)
            already_exists = key in set(ecr_templates.list_template_sheets())
            no_duration = sum(1 for s in ecr_parsed.steps if s.op_minutes == 0)
            st.write(f"**Equipment:** {ecr_parsed.equipment_name} → category **{key}** · "
                     f"**{len(ecr_parsed.steps)} cleaning steps** parsed, {no_duration} with no "
                     f"duration detected.")
            if already_exists:
                with SessionLocal() as session:
                    edited_since_import = (
                        session.query(AuditLog)
                        .filter(AuditLog.action == "UPDATE_ECR_TEMPLATE", AuditLog.details.contains(f"'{key}'"))
                        .count() > 0
                    )
                if edited_since_import:
                    st.error(f"Cleaning template '{key}' already exists AND has manual edits saved on the "
                             f"**ECR Master** page. Confirming here will OVERWRITE those edits with a fresh "
                             f"parse of this file. Cancel and edit directly on ECR Master instead if you "
                             f"don't want that.")
                else:
                    st.warning(f"A cleaning template for '{key}' already exists — confirming will REPLACE it.")

            st.dataframe(
                pd.DataFrame([{
                    "Step": i + 1, "Operation": s.text, "Time (min)": round(s.op_minutes),
                    "Source": s.method,
                } for i, s in enumerate(ecr_parsed.steps)]),
                hide_index=True, width='stretch', height=300,
            )

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("Confirm Import", type="primary", key="ecr_confirm"):
                    steps = [ecr_templates.TemplateStep(name=s.text, op_minutes=s.op_minutes)
                             for s in ecr_parsed.steps]
                    ecr_templates.write_template(key, steps)
                    with SessionLocal() as session:
                        session.add(AuditLog(
                            action="IMPORT_ECR_TEMPLATE",
                            details=(f"{current_user['username']} imported cleaning template '{key}' from "
                                      f"{Path(ecr_target).name} ({len(steps)} steps)"),
                        ))
                        session.commit()
                    st.session_state.pop("ecr_import_target", None)
                    st.session_state.pop("ecr_import_parsed", None)
                    st.success(f"Imported cleaning template for '{key}'.")
                    st.rerun()
            with btn_col2:
                if st.button("Cancel", key="ecr_cancel"):
                    st.session_state.pop("ecr_import_target", None)
                    st.session_state.pop("ecr_import_parsed", None)
                    st.rerun()

with tab_add:
    with st.form("add_equipment"):
        col1, col2, col3 = st.columns(3)
        with col1:
            new_id = st.text_input("Equipment ID (unique)", placeholder="e.g. R-15")
            category = st.selectbox("Category", CATEGORIES)
        with col2:
            subtype = st.selectbox("Subtype (reactors only)", ["", "GLR", "SSR"])
            capacity = st.number_input("Capacity (L)", min_value=0.0, value=0.0, step=100.0)
        with col3:
            area = st.selectbox("Area", AREAS)
            notes = st.text_input("Notes (optional)")
        add_submitted = st.form_submit_button("Add Equipment", type="primary")

    if add_submitted:
        new_id = new_id.strip()
        if not new_id:
            st.error("Equipment ID is required.")
        else:
            with SessionLocal() as session:
                if session.get(Equipment, new_id) is not None:
                    st.error(f"Equipment ID '{new_id}' already exists.")
                else:
                    session.add(Equipment(
                        id=new_id, category=category,
                        subtype=(subtype or None) if category == "Reactor" else None,
                        capacity_l=capacity or None, area=area, status="Active",
                        notes=notes or None,
                    ))
                    session.add(AuditLog(action="ADD_EQUIPMENT",
                                          details=f"{current_user['username']} added {new_id} ({category}, {area})"))
                    session.commit()
                    st.success(f"Added equipment '{new_id}'.")
                    st.rerun()

with tab_manage:
    with SessionLocal() as session:
        ids = [e.id for e in session.query(Equipment).order_by(Equipment.id).all()]

    if not ids:
        st.info("No equipment yet.")
    else:
        selected = st.selectbox("Select equipment", ids)
        with SessionLocal() as session:
            e = session.get(Equipment, selected)
            current_status = e.status
            has_history = session.query(Allocation).filter(Allocation.equipment_id == selected).count() > 0

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Change status**")
            new_status = st.selectbox("Status", ["Active", "Down", "Retired"],
                                       index=["Active", "Down", "Retired"].index(current_status))
            if st.button("Update status"):
                with SessionLocal() as session:
                    eq = session.get(Equipment, selected)
                    eq.status = new_status
                    session.add(AuditLog(action="UPDATE_EQUIPMENT_STATUS",
                                          details=f"{current_user['username']} set {selected} -> {new_status}"))
                    session.commit()
                st.success(f"'{selected}' status set to {new_status}.")
                st.rerun()
        with col2:
            st.markdown("**Delete**")
            if has_history:
                st.warning("This equipment has schedule history and can't be hard-deleted. "
                           "Set status to **Retired** (left) to take it out of future scheduling instead.")
            else:
                confirm = st.checkbox(f"Confirm permanent delete of '{selected}'")
                if st.button("Delete permanently", disabled=not confirm):
                    with SessionLocal() as session:
                        session.query(Equipment).filter(Equipment.id == selected).delete()
                        session.add(AuditLog(action="DELETE_EQUIPMENT",
                                              details=f"{current_user['username']} deleted {selected}"))
                        session.commit()
                    st.success(f"Deleted '{selected}'.")
                    st.rerun()
