"""Run the named analytical queries in database/queries.sql and return pandas DataFrames.

    from denguewatch.db.queries import run_query
    run_query("monthly_seasonality", state="Kerala")

Keeping the SQL in one .sql file means the same queries can be read and run
directly in psql/pgAdmin, and are reused unchanged by the API and dashboard.
"""

from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from denguewatch import config
from denguewatch.db.connection import get_engine

QUERIES_SQL = config.PROJECT_ROOT / "database" / "queries.sql"

# Default values for optional parameters
DEFAULTS = {"state": None, "district": None, "limit": 20, "min_years": 5, "top_n": 3}


@lru_cache(maxsize=1)
def load_queries() -> dict[str, str]:
    """Parse queries.sql into {name: sql} using the '-- name: xxx' markers."""
    queries: dict[str, str] = {}
    blocks = re.split(r"^--\s*name:\s*(\w+)\s*$", QUERIES_SQL.read_text(encoding="utf-8"), flags=re.M)
    for name, body in zip(blocks[1::2], blocks[2::2]):
        queries[name] = body.strip().rstrip(";")
    return queries


def query_names() -> list[str]:
    return list(load_queries())


def run_query(name: str, engine: Engine | None = None, **params) -> pd.DataFrame:
    """Run one named query. Unknown names raise KeyError; parameters are bound safely."""
    sql = load_queries()[name]
    needed = set(re.findall(r"(?<!:):(\w+)", sql))
    values = {k: params.get(k, DEFAULTS.get(k)) for k in needed}
    with (engine or get_engine()).connect() as conn:
        return pd.read_sql(text(sql), conn, params=values)
