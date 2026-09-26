"""BMR Master: Admin has full access (add/delete products, upload BMRs, edit
every recipe field). Manager can only edit an existing product's Operation
Time, Cleaning Time, and Actual Temperature — everything else here is
read-only or hidden."""
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from batch_planner import bmr, recipes
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import AuditLog, Equipment, Order, Product

st.set_page_config(page_title="BMR Master", page_icon="📦", layout="wide")
current_user = require_login(min_role=["Admin", "Manager", "Temp Editor"])
is_admin = current_user["role"] == "Admin"

st.title("📦 BMR Master")
if not is_admin:
    st.caption(f"Signed in as {current_user['role']}: you can edit Operation Time, Cleaning "
               "Time, and Actual Temperature for any existing product's recipe. Everything "
               "else is read-only.")

with SessionLocal() as session:
    products = session.query(Product).order_by(Product.code).all()
    st.dataframe(
        [{"Code": p.code, "Name": p.name, "Batch Size (kg)": p.batch_size_kg,
          "Recipe Sheet": p.recipe_sheet, "Active": p.active} for p in products],
        hide_index=True, width='stretch',
    )

st.divider()

if is_admin:
    tab_import, tab_add, tab_edit, tab_delete = st.tabs(
        ["Upload BMR", "Add Product", "Edit Product / Recipe", "Delete Product"]
    )
else:
    tab_edit = st.container()
    tab_import = tab_add = tab_delete = None

def _clear_bmr_upload() -> None:
    for key in ("bmr_import_target", "bmr_import_parsed", "bmr_upload_sig"):
        st.session_state.pop(key, None)


if tab_import is not None:
    with tab_import:
        st.caption("Attach a Master BMR and it is parsed straight away — check the "
                   "operations it read below, correct the product details, then confirm "
                   "to save it as a product with its recipe.")

        uploaded = st.file_uploader(
            "Master BMR document", type=["docx", "doc"], key="bmr_upload_widget",
            help="Legacy .doc needs Microsoft Word installed on the machine running the "
                 "app, so it only works locally. .docx works everywhere.",
        )

        with SessionLocal() as session:
            existing_codes = {p.code for p in session.query(Product).all()}

        if uploaded is not None:
            # Re-parse only when the attachment actually changes, otherwise every
            # widget interaction on this page would throw away edits made to the
            # product fields below.
            signature = (uploaded.name, uploaded.size)
            if st.session_state.get("bmr_upload_sig") != signature:
                # Parsing reads the *filename* as well as the contents — the stage
                # suffix ("... Stage-I") and the product-code fallback both come from
                # it — so the temp copy has to keep the name the user uploaded.
                tmp_dir = Path(tempfile.mkdtemp(prefix="bmr_upload_"))
                saved = tmp_dir / Path(uploaded.name).name
                saved.write_bytes(uploaded.getvalue())
                st.session_state["bmr_upload_sig"] = signature
                st.session_state["bmr_import_target"] = str(saved)
                st.session_state.pop("bmr_import_parsed", None)
                st.rerun()
        elif st.session_state.get("bmr_upload_sig") is not None:
            # Attachment cleared with the widget's x — drop the staged parse too.
            _clear_bmr_upload()
            st.rerun()

        target = st.session_state.get("bmr_import_target")
        if target:
            st.divider()
            st.markdown(f"#### Importing: {Path(target).name}")
            if "bmr_import_parsed" not in st.session_state:
                try:
                    st.session_state["bmr_import_parsed"] = bmr.parse_bmr_document(target)
                except bmr.BMRParseError as exc:
                    st.error(str(exc))
                    st.session_state["bmr_import_parsed"] = None

            parsed = st.session_state.get("bmr_import_parsed")
            if parsed is not None:
                col1, col2, col3 = st.columns(3)
                with col1:
                    import_code = st.text_input("Product code", value=parsed.product_code, key="import_code")
                with col2:
                    import_name = st.text_input("Product name", value=parsed.product_name, key="import_name")
                with col3:
                    import_batch_size = st.number_input(
                        "Batch size (kg)", min_value=0.0, value=float(parsed.batch_size_kg or 0.0),
                        step=10.0, key="import_batch_size",
                        help="Read from the document header; 0 means it wasn't found — enter it manually.",
                    )

                with SessionLocal() as session:
                    valid_ids = {e.id for e in session.query(Equipment).all()}
                unknown_ids = sorted({eid for o in parsed.operations for eid in o.equipment_ids} - valid_ids)
                no_duration = sum(1 for o in parsed.operations if o.op_minutes == 0)

                st.write(f"**{len(parsed.operations)} operations** parsed, batch no. prefix "
                         f"`{parsed.batch_no_prefix or '(none found)'}`, {no_duration} with no duration detected.")
                if unknown_ids:
                    st.warning("Equipment ID(s) not yet registered on the Equipment page (recipe "
                               "will still save, but scheduling will fail until these exist): "
                               + ", ".join(unknown_ids))

                preview_df = pd.DataFrame([{
                    "Operation": o.text,
                    "Time (min)": round(o.op_minutes),
                    "Equipment": ", ".join(o.equipment_ids),
                    "Temperature": o.temperature,
                    "Source": o.method,
                } for o in parsed.operations])
                st.dataframe(preview_df, hide_index=True, width='stretch', height=300)

                code_clean = import_code.strip()
                already_exists = code_clean in existing_codes
                if already_exists:
                    with SessionLocal() as session:
                        edited_since_import = (
                            session.query(AuditLog)
                            .filter(AuditLog.action == "UPDATE_RECIPE", AuditLog.details.contains(f" {code_clean}"))
                            .count() > 0
                        )
                    if edited_since_import:
                        st.error(f"Product '{code_clean}' already exists AND has manual recipe edits saved. "
                                f"Confirming here will OVERWRITE those edits with a fresh parse of this file. "
                                f"Cancel and edit directly in 'Edit Product / Recipe' instead if you don't want that.")
                    else:
                        st.warning(f"Product '{code_clean}' already exists — confirming will REPLACE its recipe.")

                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    confirm_disabled = not code_clean or not import_name.strip() or import_batch_size <= 0
                    if st.button("Confirm Import", type="primary", disabled=confirm_disabled):
                        sheet = recipes.sheet_name_for(code_clean)
                        new_stages = [
                            recipes.Stage(name=o.text, op_minutes=o.op_minutes,
                                          clean_minutes=bmr.default_clean_minutes(o.text, o.equipment_ids),
                                          equipment_ids=o.equipment_ids, temperature=o.temperature)
                            for o in parsed.operations
                        ]
                        recipes.write_recipe(sheet, new_stages)
                        with SessionLocal() as session:
                            if session.get(Product, code_clean) is not None:
                                session.query(Product).filter(Product.code == code_clean).delete()
                            session.add(Product(code=code_clean, name=import_name.strip(),
                                                 batch_size_kg=import_batch_size, recipe_sheet=sheet, active=True))
                            session.add(AuditLog(
                                action="IMPORT_BMR",
                                details=(f"{current_user['username']} imported {code_clean} from "
                                          f"{Path(target).name} ({len(new_stages)} operations)"),
                            ))
                            session.commit()
                        _clear_bmr_upload()
                        st.success(f"Imported '{code_clean}'. Review it under 'Edit Product / Recipe' before scheduling real batches.")
                        st.rerun()
                with btn_col2:
                    if st.button("Cancel"):
                        _clear_bmr_upload()
                        st.rerun()

if tab_add is not None:
    with tab_add:
        with st.form("add_product"):
            code = st.text_input("Product code (unique)", placeholder="e.g. API-002")
            name = st.text_input("Product name")
            batch_size = st.number_input("Standard batch size (kg)", min_value=0.1, value=100.0, step=10.0)
            add_submitted = st.form_submit_button("Add Product", type="primary")
        if add_submitted:
            code = code.strip()
            if not code or not name.strip():
                st.error("Product code and name are required.")
            else:
                with SessionLocal() as session:
                    if session.get(Product, code) is not None:
                        st.error(f"Product code '{code}' already exists.")
                    else:
                        sheet = recipes.sheet_name_for(code)
                        if sheet in recipes.list_product_sheets():
                            recipes.delete_product_sheet(sheet)
                        recipes.create_product_sheet(sheet, starter_stages=1)
                        session.add(Product(code=code, name=name.strip(), batch_size_kg=batch_size,
                                             recipe_sheet=sheet, active=True))
                        session.add(AuditLog(action="ADD_PRODUCT",
                                              details=f"{current_user['username']} added {code} ({name})"))
                        session.commit()
                        st.success(f"Added product '{code}'. Fill in its recipe under 'Edit Product / Recipe'.")
                        st.rerun()

with tab_edit:
    with SessionLocal() as session:
        # Every product is editable here regardless of role. What a Manager
        # may change is still narrowed column-by-column further down; their
        # product assignment scopes the Batches page, not this one.
        edit_products = session.query(Product).order_by(Product.name).all()
        product_options = {f"{p.name} ({p.code})": p.code for p in edit_products}

    if not product_options:
        st.info("No products yet.")
    else:
        selected_label = st.selectbox("Select product", list(product_options.keys()), key="edit_product_select")
        selected = product_options[selected_label]

        if st.session_state.get("_edit_product_last_selected") != selected:
            # Force a never-before-seen data_editor key on every product switch. Changing
            # `key` and `data` in the same rerun can otherwise leave the editor's frontend
            # grid showing the previous product's rows even though the Python-side value is
            # correct — a nonce sidesteps that instead of relying on remount detection.
            st.session_state["_edit_product_last_selected"] = selected
            st.session_state["_edit_product_editor_nonce"] = st.session_state.get("_edit_product_editor_nonce", 0) + 1
        editor_key = f"editor_{selected}_{st.session_state.get('_edit_product_editor_nonce', 0)}"

        with SessionLocal() as session:
            p = session.get(Product, selected)
            batch_val, active_val, sheet = p.batch_size_kg, p.active, p.recipe_sheet

        st.markdown(f"#### {selected_label}")
        if batch_val <= 0:
            st.warning("This product has no batch size set yet (imported without one) — "
                       "it can't be scheduled until you set a real value below.")
        if is_admin:
            col1, col2 = st.columns(2)
            with col1:
                new_batch = st.number_input("Batch size (kg)", min_value=0.0, value=float(batch_val),
                                             step=10.0, key="edit_batch")
            with col2:
                new_active = st.checkbox("Active", value=active_val, key="edit_active")
            if st.button("Save product details"):
                with SessionLocal() as session:
                    pr = session.get(Product, selected)
                    pr.batch_size_kg, pr.active = new_batch, new_active
                    session.add(AuditLog(action="UPDATE_PRODUCT",
                                          details=f"{current_user['username']} updated {selected}"))
                    session.commit()
                st.success("Product details saved.")
                st.rerun()
        else:
            st.caption(f"Batch size: {batch_val:g} kg · {'Active' if active_val else 'Inactive'}")

        st.markdown("#### Recipe: Operations")
        if is_admin:
            st.caption("One row per operation, in the order they run in the BMR. Equipment IDs must "
                       "match IDs on the Equipment page, comma-separated (e.g. `R-01, R-02, R-03`). "
                       "An operation left at 0 min but with equipment assigned is scheduled as "
                       "5 min ±2% (re-drawn per batch), not as instantaneous.")
        else:
            st.caption("You can edit Operation Time, Cleaning Time, and Actual Temperature. Operation "
                       "text, Equipment IDs, and Standard Temperature are set by an Admin.")

        bmr_key = f"bmr_stages_{selected}"
        if is_admin:
            with st.expander("📄 Upload a BMR (.docx) to auto-fill Operation + Duration"):
                st.caption(
                    "Reads the Operation column of the uploaded BMR exactly as written. Duration is "
                    "computed from the operation text itself, checked in order: an explicit duration "
                    "('for 8 hours', 'for one hour'); a quantity in kg (200 kg = 60 min); a volume in L "
                    "(400 L = 25 min); an analysis/QC mention (LOD = 60 min, Moisture = 40 min, otherwise "
                    "300 min); a heating/chilling/cooling mention (120 min — these also vary +/-5% per "
                    "batch at schedule time); a charge/load mention (5 min); a 'check ...' mention "
                    "(20 min); otherwise 0, for manual entry. Equipment IDs in brackets (e.g. "
                    "'[PR/API/SSR/01]') are read automatically and carry forward to later operations "
                    "until a different code appears — check the assignments below before saving. For "
                    "whole BMR files (including legacy .doc), use the **Upload BMR** tab "
                    "instead — it also reads product code/name/batch size and standard temperature."
                )
                uploaded = st.file_uploader("BMR Word document (.docx)", type=["docx"], key=f"uploader_{selected}")
                if uploaded is not None and st.button("Parse BMR", key=f"parse_{selected}"):
                    try:
                        parsed = bmr.parse_bmr_docx(uploaded)
                    except bmr.BMRParseError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state[bmr_key] = parsed
                        st.success(f"Parsed {len(parsed)} operation(s). Review the durations below, "
                                   f"assign Equipment IDs, then Save Recipe.")
                        st.rerun()

        if is_admin and bmr_key in st.session_state:
            parsed_ops = st.session_state[bmr_key]
            st.info("Showing operations parsed from the uploaded BMR (not yet saved). Check the "
                    "parsed durations, assign Equipment IDs, then **Save Recipe** below.")
            df = pd.DataFrame([{
                "Operation": p.text,
                "Operation Time (min)": p.op_minutes,
                "Cleaning Time (min)": 0.0,
                "Equipment IDs (comma-separated)": ", ".join(p.equipment_ids),
                "Standard Temperature": p.temperature,
                "Actual Temperature": "",
                "Duration Source": p.method,
            } for p in parsed_ops])
            if st.button("Discard parsed BMR, go back to saved recipe", key=f"discard_{selected}"):
                del st.session_state[bmr_key]
                st.rerun()
        else:
            stages = recipes.read_recipe(sheet)
            df = pd.DataFrame([{
                "Operation": s.name,
                "Operation Time (min)": s.op_minutes,
                "Cleaning Time (min)": s.clean_minutes,
                "Equipment IDs (comma-separated)": ", ".join(s.equipment_ids),
                "Standard Temperature": s.temperature,
                "Actual Temperature": s.actual_temperature,
            } for s in stages])
            if df.empty:
                df = pd.DataFrame([{"Operation": "Operation 1", "Operation Time (min)": 0,
                                    "Cleaning Time (min)": 0, "Equipment IDs (comma-separated)": "",
                                    "Standard Temperature": "", "Actual Temperature": ""}])

        if is_admin:
            disabled_cols = ["Duration Source"] if "Duration Source" in df.columns else []
            num_rows = "dynamic"
        else:
            disabled_cols = ["Operation", "Equipment IDs (comma-separated)", "Standard Temperature"]
            num_rows = "fixed"

        edited = st.data_editor(
            df, num_rows=num_rows, width='stretch', key=editor_key, disabled=disabled_cols,
        )

        if st.button("Save recipe", type="primary"):
            with SessionLocal() as session:
                valid_ids = {e.id for e in session.query(Equipment).all()}

            new_stages = []
            unknown = set()
            for _, row in edited.iterrows():
                name = str(row["Operation"]).strip()
                if not name or name.lower() == "nan":
                    continue
                ids = [i.strip() for i in str(row["Equipment IDs (comma-separated)"]).split(",") if i.strip()]
                unknown |= {i for i in ids if i not in valid_ids}
                op = pd.to_numeric(row["Operation Time (min)"], errors="coerce")
                clean = pd.to_numeric(row["Cleaning Time (min)"], errors="coerce")
                temperature = str(row.get("Standard Temperature", "") or "").strip()
                if temperature.lower() == "nan":
                    temperature = ""
                actual_temperature = str(row.get("Actual Temperature", "") or "").strip()
                if actual_temperature.lower() == "nan":
                    actual_temperature = ""
                new_stages.append(recipes.Stage(
                    name=name,
                    op_minutes=round(float(op)) if pd.notna(op) else 0.0,
                    clean_minutes=round(float(clean)) if pd.notna(clean) else 0.0,
                    equipment_ids=ids,
                    temperature=temperature,
                    actual_temperature=actual_temperature,
                ))
            if unknown:
                st.error(f"Unknown equipment ID(s): {', '.join(sorted(unknown))}. "
                        f"Add them under Equipment first.")
            else:
                recipes.write_recipe(sheet, new_stages)
                if bmr_key in st.session_state:
                    del st.session_state[bmr_key]
                with SessionLocal() as session:
                    session.add(AuditLog(action="UPDATE_RECIPE",
                                          details=f"{current_user['username']} updated recipe for {selected}"))
                    session.commit()
                missing_equipment = [s.name for s in new_stages if not s.equipment_ids]
                st.success("Recipe saved.")
                if missing_equipment:
                    st.warning("These operations have no Equipment IDs yet — scheduling will fail "
                              "until you add some: " + "; ".join(missing_equipment))
                st.rerun()

if tab_delete is not None:
    with tab_delete:
        with SessionLocal() as session:
            delete_options = {f"{p.name} ({p.code})": p.code
                               for p in session.query(Product).order_by(Product.name).all()}

        if not delete_options:
            st.info("No products yet.")
        else:
            delete_label = st.selectbox("Select product to delete", list(delete_options.keys()), key="delete_select")
            selected = delete_options[delete_label]
            with SessionLocal() as session:
                order_count = session.query(Order).filter(Order.product_code == selected).count()

            if order_count > 0:
                st.warning(f"'{selected}' has {order_count} order(s) in schedule history and can't be deleted. "
                          f"Untick 'Active' under Edit Product to retire it from new scheduling instead.")
            else:
                confirm = st.checkbox(f"Confirm permanent delete of '{selected}' and its recipe")
                if st.button("Delete product permanently", disabled=not confirm):
                    with SessionLocal() as session:
                        p = session.get(Product, selected)
                        sheet = p.recipe_sheet
                        session.query(Product).filter(Product.code == selected).delete()
                        session.add(AuditLog(action="DELETE_PRODUCT",
                                              details=f"{current_user['username']} deleted {selected}"))
                        session.commit()
                    recipes.delete_product_sheet(sheet)
                    st.success(f"Deleted '{selected}'.")
                    st.rerun()
