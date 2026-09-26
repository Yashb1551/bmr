"""Admin: manage login accounts (add, reset password, change role, delete)."""
import streamlit as st

from batch_planner import security
from batch_planner.auth import require_login
from batch_planner.db import SessionLocal
from batch_planner.models import AuditLog, Product, User

st.set_page_config(page_title="Users", page_icon="👤", layout="wide")
current_user = require_login(min_role="Admin")

st.title("👤 Users")
st.caption("**Admin** — full access everywhere. **Manager** — everything a Temp Editor can do, plus reschedule/pause/resume/delete batches on **Batches**. **Temp Editor** — a temporary editor: can edit any product's Operation Time, Cleaning Time and Actual Temperature on **BMR Master** (Operation text, Equipment IDs, Standard Temperature, add/delete products and uploads stay Admin-only) and edit cleaning-step templates on **ECR Master**, but cannot touch a booked batch. **Planner** — schedule batches and look up any batch's BMR/ECR output, plus the equipment map/timeline; no edit or delete rights anywhere. Every role can schedule any product and see the whole plant schedule; only Admin manages accounts.")

# Least to most privileged. "Temp Editor" is a temporary editor: a
# Manager's recipe- and cleaning-template rights, without the Batches page.
ROLES = ["Planner", "Temp Editor", "Manager", "Admin"]

with SessionLocal() as session:
    users = session.query(User).order_by(User.username).all()
    st.dataframe(
        [{"Username": u.username, "Name": u.display_name, "Role": u.role, "Active": u.active} for u in users],
        hide_index=True, width='stretch',
    )

st.divider()
tab_add, tab_manage = st.tabs(["Add User", "Reset Password / Role / Delete"])

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
