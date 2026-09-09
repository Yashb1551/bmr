"""First-run seed data. Only runs when the User table is empty — never
touches data once it exists (use the admin pages after that). Equipment,
products and recipes are entered for real via the app; only the login
account is auto-seeded so a genuinely fresh database is usable immediately.

The default password is intentionally weak and is published in this source
tree, so set ADMIN_PASSWORD (Streamlit secret) or BATCH_PLANNER_ADMIN_PASSWORD
(env var) for any deployment, and change it from the Users page on first
login regardless.
"""
import os

from sqlalchemy.orm import Session

from . import security
from .models import User

DEFAULT_ADMIN_USERNAME = "admin"
_FALLBACK_ADMIN_PASSWORD = "ChangeMe123!"


def _default_admin_password() -> str:
    pw = os.environ.get("BATCH_PLANNER_ADMIN_PASSWORD")
    if not pw:
        try:
            import streamlit as st

            pw = st.secrets.get("ADMIN_PASSWORD")
        except Exception:
            pw = None
    return pw or _FALLBACK_ADMIN_PASSWORD


def seed_default_admin(session: Session) -> None:
    session.add(User(
        username=DEFAULT_ADMIN_USERNAME,
        password_hash=security.hash_password(_default_admin_password()),
        display_name="Administrator",
        role="Admin",
        active=True,
    ))
    session.commit()


def seed_if_empty(session: Session) -> None:
    if session.query(User).count() == 0:
        seed_default_admin(session)
