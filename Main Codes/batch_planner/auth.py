"""Login gate. Call `require_login()` at the top of every page, right after
st.set_page_config(...). Blocks the rest of the page behind a login form,
optionally enforces a role, and renders the signed-in-as/log-out sidebar."""
import streamlit as st

from datetime import datetime

from . import scheduler, security
from .db import SessionLocal, init_db
from .models import Equipment, User
from .seed import seed_if_empty


def _ensure_ready() -> None:
    init_db()
    with SessionLocal() as session:
        seed_if_empty(session)


def _login_form() -> None:
    st.title("🔒 Batch Planner — Sign in")
    st.caption("Pharmaceutical API Manufacturing Unit")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        with SessionLocal() as session:
            user = session.get(User, username.strip())
        if user is None or not user.active or not security.verify_password(password, user.password_hash):
            st.error("Invalid username or password.")
        else:
            st.session_state["user"] = {
                "username": user.username,
                "display_name": user.display_name,
                "role": user.role,
            }
            st.rerun()
    st.stop()


def _render_plant_snapshot() -> None:
    """Compact, read-only equipment-status summary shown at the top of the
    sidebar on every page (not just Scheduler) — a smaller version of what
    used to be a full-width section at the bottom of the Scheduler page."""
    with SessionLocal() as session:
        equipment = session.query(Equipment).all()
        now = datetime.now()
        counts = {"Free": 0, "Running": 0, "Cleaning": 0, "Down": 0, "Retired": 0}
        for e in equipment:
            info = scheduler.equipment_status_at(session, e, now)
            counts[info["status"]] = counts.get(info["status"], 0) + 1

    st.caption("**Plant snapshot**")
    st.caption(
        f"🟢 {counts['Free']} Free &nbsp;·&nbsp; 🔵 {counts['Running']} Running &nbsp;·&nbsp; "
        f"🟡 {counts['Cleaning']} Cleaning &nbsp;·&nbsp; 🔴 {counts['Down']} Down &nbsp;·&nbsp; "
        f"⚪ {counts['Retired']} Retired",
        unsafe_allow_html=True,
    )
    st.divider()


def _render_sidebar(user: dict) -> None:
    with st.sidebar:
        _render_plant_snapshot()
        st.markdown(f"Signed in as **{user['display_name']}**  \n`{user['role']}`")
        if st.button("Log out"):
            del st.session_state["user"]
            st.rerun()
        st.divider()


def _inject_copy_deterrents() -> None:
    """Browser-side deterrents only: disables text selection, right-click,
    and Ctrl+C/P/S. This does NOT and CANNOT block screenshots — no web
    page can prevent an OS screenshot tool, a phone camera, or a browser's
    own print-to-PDF/dev-tools. Treat this as a mild speed bump against
    casual copy-paste, not a security control."""
    st.markdown(
        """
        <style>
        * { -webkit-user-select: none !important; user-select: none !important; }
        </style>
        <script>
        document.addEventListener('contextmenu', e => e.preventDefault());
        document.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && ['c', 'p', 's'].includes(e.key.toLowerCase())) {
                e.preventDefault();
            }
        });
        </script>
        """,
        unsafe_allow_html=True,
    )


def require_login(min_role: str | list[str] | None = None) -> dict:
    """Returns the signed-in user dict ({username, display_name, role}).
    Stops page execution if not logged in, or logged in without an allowed
    role. `min_role` (despite the name) is an exact-match allow-list, not a
    hierarchy — pass a single role string or a list of allowed roles."""
    _ensure_ready()

    if "user" not in st.session_state:
        _login_form()

    user = st.session_state["user"]
    if min_role is not None:
        allowed_roles = [min_role] if isinstance(min_role, str) else min_role
        if user["role"] not in allowed_roles:
            st.error(f"This page is restricted to {'/'.join(allowed_roles)} users. "
                     f"You're signed in as {user['display_name']} ({user['role']}).")
            st.stop()

    _inject_copy_deterrents()
    _render_sidebar(user)
    return user
