"""Admin: manage login accounts (add, reset password, change role, delete)."""
import streamlit as st

from batch_planner import security
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import AuditLog, Product, User

st.set_page_config(page_title="Users", page_icon="👤", layout="wide")
current_user = require_login(min_role="Admin")

st.title("👤 Users")
st.caption("**Admin** — full access everywhere. **Manager** — can edit an existing "
           "product's Operation Time, Cleaning Time, and Actual Temperature on the Products "
           "page; everything else there (Operation text, Equipment IDs, Standard Temperature, "
           "add/delete products, imports) is read-only or hidden. Can also edit ECR cleaning "
           "templates on **ECR Master**, and reschedule/pause/resume/delete batches on "
           "**Batches** — plus everything a Planner can do. A Manager can also be "
           "restricted to a specific group of products via the **Product Access** tab below — "
           "once restricted, they can only start/reschedule/delete batches and edit recipes for "
           "their assigned products, though everyone can still view the equipment map and "
           "schedule for all products. **Planner** — schedule batches and look up any batch's "
           "BMR/ECR output at any time, plus the equipment map/timeline; no edit or delete "
           "rights anywhere.")

ROLES = ["Planner", "Manager", "Admin"]

with SessionLocal() as session:
    users = session.query(User).order_by(User.username).all()
    st.dataframe(
        [{"Username": u.username, "Name": u.display_name, "Role": u.role, "Active": u.active} for u in users],
        hide_index=True, width='stretch',
    )

st.divider()
tab_add, tab_manage, tab_access = st.tabs(["Add User", "Reset Password / Role / Delete", "Product Access"])

with tab_add:
    with st.form("add_user", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_username = st.text_input("Username (unique)", key="add_username")
            display_name = st.text_input("Display name", key="add_display_name")
            role = st.selectbox("Role", ROLES, key="add_role")
        with col2:
            password = st.text_input("Password", type="password", key="add_password")
            confirm = st.text_input("Confirm password", type="password", key="add_confirm")
        add_submitted = st.form_submit_button("Add User", type="primary")

    if add_submitted:
        new_username = new_username.strip()
        if not new_username or not display_name.strip() or not password:
            st.error("Username, display name, and password are required.")
        elif password != confirm:
            st.error("Passwords don't match.")
        elif len(password) < 8:
            st.error("Password must be at least 8 characters.")
        else:
            with SessionLocal() as session:
                if session.get(User, new_username) is not None:
                    st.error(f"Username '{new_username}' already exists.")
                else:
                    session.add(User(
                        username=new_username, password_hash=security.hash_password(password),
                        display_name=display_name.strip(), role=role, active=True,
                    ))
                    session.add(AuditLog(action="ADD_USER",
                                          details=f"{current_user['username']} added user {new_username} ({role})"))
                    session.commit()
                    # Belt-and-suspenders alongside clear_on_submit: explicitly drop the
                    # password fields so the plaintext value can't linger in session_state.
                    del st.session_state["add_password"]
                    del st.session_state["add_confirm"]
                    st.success(f"Added user '{new_username}'.")
                    st.rerun()

with tab_manage:
    with SessionLocal() as session:
        usernames = [u.username for u in session.query(User).order_by(User.username).all()]

    if not usernames:
        st.info("No users yet.")
    else:
        selected = st.selectbox("Select user", usernames)
        with SessionLocal() as session:
            u = session.get(User, selected)
            u_role, u_active = u.role, u.active

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**Reset password**")
            new_pw = st.text_input("New password", type="password", key="reset_pw")
            new_pw2 = st.text_input("Confirm new password", type="password", key="reset_pw2")
            if st.button("Reset password"):
                if not new_pw or new_pw != new_pw2:
                    st.error("Passwords are empty or don't match.")
                elif len(new_pw) < 8:
                    st.error("Password must be at least 8 characters.")
                else:
                    with SessionLocal() as session:
                        target = session.get(User, selected)
                        target.password_hash = security.hash_password(new_pw)
                        session.add(AuditLog(action="RESET_PASSWORD",
                                              details=f"{current_user['username']} reset password for {selected}"))
                        session.commit()
                    # Keyed widgets don't clear on rerun by themselves — drop the session_state
                    # entries so the plaintext password doesn't linger on screen after the reset.
                    del st.session_state["reset_pw"]
                    del st.session_state["reset_pw2"]
                    st.success(f"Password reset for '{selected}'.")
                    st.rerun()

        with col2:
            st.markdown("**Role & status**")
            new_role = st.selectbox("Role", ROLES, index=ROLES.index(u_role), key="role_select")
            new_active = st.checkbox("Active", value=u_active, key="active_check")
            if st.button("Save role/status"):
                with SessionLocal() as session:
                    admin_count = session.query(User).filter(User.role == "Admin", User.active == True).count()
                    target = session.get(User, selected)
                    demoting_last_admin = (
                        target.role == "Admin" and (new_role != "Admin" or not new_active) and admin_count <= 1
                    )
                    if demoting_last_admin:
                        st.error("Can't remove the last active Admin account.")
                    else:
                        target.role, target.active = new_role, new_active
                        session.add(AuditLog(
                            action="UPDATE_USER",
                            details=f"{current_user['username']} updated {selected} -> role={new_role}, active={new_active}",
                        ))
                        session.commit()
                        st.success(f"Updated '{selected}'.")
                        st.rerun()

        with col3:
            st.markdown("**Delete**")
            if selected == current_user["username"]:
                st.warning("You can't delete your own account while signed in as it.")
            else:
                confirm_del = st.checkbox(f"Confirm permanent delete of '{selected}'")
                if st.button("Delete permanently", disabled=not confirm_del):
                    with SessionLocal() as session:
                        admin_count = session.query(User).filter(User.role == "Admin", User.active == True).count()
                        target = session.get(User, selected)
                        if target.role == "Admin" and admin_count <= 1:
                            st.error("Can't delete the last active Admin account.")
                        else:
                            session.query(User).filter(User.username == selected).delete()
                            session.add(AuditLog(action="DELETE_USER",
                                                  details=f"{current_user['username']} deleted user {selected}"))
                            session.commit()
                            st.success(f"Deleted '{selected}'.")
                            st.rerun()

with tab_access:
    st.caption("Restrict a **Manager** account to a specific group of products. Once restricted, "
               "that Manager can only start, reschedule/edit, delete, and pause batches — and edit "
               "recipes — for their assigned products. Everyone (including restricted Managers) can "
               "still view the Equipment Map and Gantt Timeline for the full schedule. Admins and "
               "Planners are never restricted.")

    with SessionLocal() as session:
        manager_usernames = [
            u.username for u in session.query(User).filter(User.role == "Manager").order_by(User.username).all()
        ]
        all_products = session.query(Product).order_by(Product.name).all()
        product_labels = {f"{p.name} ({p.code})": p.code for p in all_products}
        code_to_label = {v: k for k, v in product_labels.items()}

    if not manager_usernames:
        st.info("No Manager accounts yet. Product access restrictions only apply to the 'Manager' role.")
    else:
        access_selected = st.selectbox("Select Manager", manager_usernames, key="access_user_select")
        with SessionLocal() as session:
            target_user = session.get(User, access_selected)
            current_allowed = [
                c.strip() for c in (target_user.allowed_products or "").split(",") if c.strip()
            ]

        current_labels = [code_to_label[c] for c in current_allowed if c in code_to_label]
        if not all_products:
            st.info("No products exist yet.")
        else:
            chosen_labels = st.multiselect(
                "Products this Manager may work on (leave empty = no product access yet)",
                options=list(product_labels.keys()),
                default=current_labels,
                key=f"access_multiselect_{access_selected}",
            )
            if st.button("Save product access", type="primary"):
                chosen_codes = sorted({product_labels[label] for label in chosen_labels})
                with SessionLocal() as session:
                    target = session.get(User, access_selected)
                    target.allowed_products = ",".join(chosen_codes) if chosen_codes else None
                    session.add(AuditLog(
                        action="UPDATE_PRODUCT_ACCESS",
                        details=f"{current_user['username']} set product access for {access_selected} -> {chosen_codes}",
                    ))
                    session.commit()
                st.success(f"Saved product access for '{access_selected}' ({len(chosen_codes)} product(s)).")
                st.rerun()
