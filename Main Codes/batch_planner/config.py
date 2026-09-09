"""Paths shared across the app. Database/ sits next to Main Codes/ at the project root."""
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATABASE_DIR = BASE_DIR / "Database"
DATABASE_DIR.mkdir(exist_ok=True)

DB_PATH = DATABASE_DIR / "plant.db"

# Legacy Excel stores. Recipes and ECR templates now live in the database
# (see recipes.py / ecr_templates.py); these paths are only read by the
# one-time migrate_to_supabase.py script if the old files are still present.
RECIPES_PATH = DATABASE_DIR / "recipes.xlsx"
ECR_TEMPLATES_PATH = DATABASE_DIR / "ecr_templates.xlsx"


def normalise_db_url(url: str) -> str:
    """Make a pasted Supabase connection string usable as-is.

    Supabase hands you a ``postgresql://…`` URI with no driver or SSL mode on
    it. Two fixes so copy-paste just works:

    * ``postgres://`` -> ``postgresql://`` — SQLAlchemy 2.x dropped the short
      scheme, and several hosts still emit it.
    * add ``sslmode=require`` when it's missing. Supabase requires TLS; without
      the parameter libpq negotiates it anyway but won't fail closed, so we set
      it explicitly rather than relying on the server to insist.

    Anything already spelled out in the URL is left alone.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.startswith("postgresql"):
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    return urlunsplit(parts._replace(query=urlencode(query)))


def _resolve_db_url() -> str:
    """Use an external database when DATABASE_URL is set — as an env var, or a
    Streamlit secret. Required for any hosted deployment (Streamlit Community
    Cloud and similar wipe the local filesystem, and this SQLite file with it,
    on every restart/redeploy). Falls back to the local SQLite file for
    on-prem / desktop use, which is unchanged."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            import streamlit as st

            url = st.secrets.get("DATABASE_URL")
        except Exception:
            url = None
    if not url:
        return f"sqlite:///{DB_PATH}"
    return normalise_db_url(url)


DB_URL = _resolve_db_url()

BMR_FOLDER = DATABASE_DIR / "BMR"
EQUIPMENT_LIST_FOLDER = DATABASE_DIR / "Equipment List"
ECR_FOLDER = DATABASE_DIR / "ECR"
BMR_FOLDER.mkdir(exist_ok=True)
EQUIPMENT_LIST_FOLDER.mkdir(exist_ok=True)
ECR_FOLDER.mkdir(exist_ok=True)
