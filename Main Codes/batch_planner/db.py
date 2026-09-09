"""Engine/session setup. Works against either the local SQLite file or a
hosted Postgres (Supabase) — whichever ``config.DB_URL`` resolved to."""
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import DB_URL


def _engine_kwargs(url: str) -> dict:
    """Connection settings that only make sense for a networked database.

    Local SQLite keeps SQLAlchemy's defaults (they're already correct for a
    file). A hosted Postgres needs more care: Supabase's pooler drops idle
    client connections, and Streamlit keeps a process alive between reruns,
    so a pooled connection can easily go stale between one page view and the
    next. ``pool_pre_ping`` catches that; ``pool_recycle`` avoids it.
    """
    kwargs: dict = {"echo": False, "pool_pre_ping": True}
    if not url.startswith("postgresql"):
        return kwargs

    kwargs.update(
        # Small pool: Supabase's free tier caps concurrent connections, and a
        # single Streamlit app never needs many.
        pool_size=5,
        max_overflow=5,
        # Recycle well inside the pooler's idle timeout.
        pool_recycle=280,
        pool_timeout=30,
        connect_args={
            "connect_timeout": 10,
            # Named in Supabase's dashboard (Database -> Roles/Connections),
            # so it's obvious which client a connection belongs to.
            "application_name": "batch-planner",
            # Drop a connection the network silently killed rather than
            # blocking a page render on a dead socket.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
    return kwargs


engine = create_engine(DB_URL, **_engine_kwargs(DB_URL))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

_init_lock = threading.Lock()
_initialised = False


def init_db() -> None:
    """Create any missing tables. Called at the top of every page (via
    ``auth.require_login``), so it's memoised per process: against Postgres
    ``create_all`` reflects the whole schema over the network first, which is
    a few round-trips we don't want to pay on every Streamlit rerun."""
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        from . import models
        models.Base.metadata.create_all(engine)
        _initialised = True
