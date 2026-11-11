"""PostgreSQL connection helpers."""

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from denguewatch import config


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a shared SQLAlchemy engine built from DATABASE_URL."""
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in your database details."
        )
    return create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)


def check_connection() -> tuple[bool, str]:
    """Try a trivial query. Returns (ok, message) and never raises."""
    try:
        with get_engine().connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
        return True, version.split(",")[0]
    except Exception as exc:  # noqa: BLE001 - report any failure to the caller
        return False, str(exc).splitlines()[0]
