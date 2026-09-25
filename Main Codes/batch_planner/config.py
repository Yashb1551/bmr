"""Paths shared across the app. Database/ sits next to Main Codes/ at the project root."""
import importlib.util
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


# Postgres DBAPIs this app can drive, in the order we'd rather use them.
# psycopg (v3) first: it is what current SQLAlchemy reaches for by default and
# it publishes wheels for new Python versions promptly. psycopg2 second, so
# existing installs that only have it keep working untouched.
_PG_DRIVERS = ("psycopg", "psycopg2")


def _installed_pg_driver() -> str | None:
    """First driver from _PG_DRIVERS that is actually importable, else None."""
    for name in _PG_DRIVERS:
        if importlib.util.find_spec(name) is not None:
            return name
    return None


def normalise_db_url(url: str) -> str:
    """Make a pasted Supabase connection string usable as-is.

    Supabase hands you a ``postgresql://…`` URI with no driver or SSL mode on
    it. Three fixes so copy-paste just works:

    * ``postgres://`` -> ``postgresql://`` — SQLAlchemy 2.x dropped the short
      scheme, and several hosts still emit it.
    * name the DBAPI explicitly (``postgresql+psycopg://``). A bare
      ``postgresql://`` lets SQLAlchemy pick, and which one it picks changed
      between versions: older ones load psycopg2, newer ones psycopg (v3). With
      the requirements unpinned, that turns a deployment into a coin toss
      decided by whatever SQLAlchemy pip happened to install — it fails at
      import with ModuleNotFoundError if the chosen driver isn't the one that
      got installed. Pinning the scheme to a driver we can actually import
      makes the app work the same on every box.
    * add ``sslmode=require`` when it's missing. Supabase requires TLS; without
      the parameter libpq negotiates it anyway but won't fail closed, so we set
      it explicitly rather than relying on the server to insist.

    Anything already spelled out in the URL is left alone — including an
    explicit ``+driver``, so you can still force one by hand.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.startswith("postgresql"):
        return url
    parts = urlsplit(url)

    if "+" not in parts.scheme:
        driver = _installed_pg_driver()
        if driver is not None:
            parts = parts._replace(scheme=f"postgresql+{driver}")

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    return urlunsplit(parts._replace(query=urlencode(query)))


# Why each step of _resolve_db_url() did or did not supply a URL. Key *names*
# only, never values - this is rendered on the sign-in screen when the lookup
# falls through to SQLite, so that a misconfigured deployment says what is
# actually missing instead of only that something is.
DB_URL_NOTES: list[str] = []


def _resolve_db_url() -> str:
    """Use an external database when DATABASE_URL is set — as an env var, or a
    Streamlit secret. Required for any hosted deployment (Streamlit Community
    Cloud and similar wipe the local filesystem, and this SQLite file with it,
    on every restart/redeploy). Falls back to the local SQLite file for
    on-prem / desktop use, which is unchanged."""
    url = os.environ.get("DATABASE_URL")
    if url:
        DB_URL_NOTES.append("found DATABASE_URL in the environment")
        return normalise_db_url(url)
    DB_URL_NOTES.append("no DATABASE_URL environment variable")

    try:
        import streamlit as st
    except Exception as exc:  # noqa: BLE001
        DB_URL_NOTES.append(f"streamlit not importable ({type(exc).__name__})")
        DB_URL_NOTES.append("falling back to the local SQLite file")
        return f"sqlite:///{DB_PATH}"

    try:
        keys = sorted(st.secrets.keys())
    except Exception as exc:  # noqa: BLE001
        # No secrets configured at all: on Community Cloud this means the
        # Secrets box is empty, or was saved without rebooting the app.
        DB_URL_NOTES.append(f"no secrets available ({type(exc).__name__})")
        DB_URL_NOTES.append("falling back to the local SQLite file")
        return f"sqlite:///{DB_PATH}"

    DB_URL_NOTES.append(f"secrets loaded, keys = {keys or '(empty)'}")
    url = st.secrets.get("DATABASE_URL")
    if url:
        DB_URL_NOTES.append("found DATABASE_URL in secrets")
        return normalise_db_url(url)

    DB_URL_NOTES.append("secrets contain no DATABASE_URL key")
    DB_URL_NOTES.append("falling back to the local SQLite file")
    return f"sqlite:///{DB_PATH}"


DB_URL = _resolve_db_url()

BMR_FOLDER = DATABASE_DIR / "BMR"
EQUIPMENT_LIST_FOLDER = DATABASE_DIR / "Equipment List"
ECR_FOLDER = DATABASE_DIR / "ECR"
BMR_FOLDER.mkdir(exist_ok=True)
EQUIPMENT_LIST_FOLDER.mkdir(exist_ok=True)
ECR_FOLDER.mkdir(exist_ok=True)


def describe_db_target(url: str | None = None) -> str:
    """Credential-free description of what DB_URL points at, for display.

    The silent SQLite fallback below is right for a desktop run and badly
    wrong for a hosted one: if DATABASE_URL never reaches the app, every
    account in the real database simply appears not to exist, and the only
    symptom is "invalid username or password" for every login. Showing the
    target on the sign-in screen makes that misconfiguration visible instead
    of leaving it to be guessed at.

    Never includes the username or password — only scheme, host and database.
    """
    url = url or DB_URL
    if url.startswith("sqlite"):
        return f"SQLite file — {DB_PATH.name} (local, not shared)"
    parts = urlsplit(url)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    name = (parts.path or "").lstrip("/") or "?"
    return f"{parts.scheme.split('+')[0]} — {host}{port}/{name}"


def is_external_db() -> bool:
    """True when pointed at a real server rather than the local SQLite file."""
    return not DB_URL.startswith("sqlite")
