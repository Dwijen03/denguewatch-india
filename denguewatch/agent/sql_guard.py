"""Guardrails for SQL written by the AI agent.

The model's SQL is never trusted. Before it runs it must pass all of these checks:

  1. It parses as PostgreSQL and is exactly ONE statement.
  2. It is a read query (SELECT / WITH ... SELECT / UNION), with no INSERT, UPDATE, DELETE,
     DDL, COPY, SET, locking (FOR UPDATE) or SELECT ... INTO.
  3. It reads only allowed views/tables (ALLOWED_TABLES), with no schema prefixes
     (so no pg_catalog or information_schema).
  4. It calls no dangerous functions (pg_*, dblink, lo_*, set_config, file access ...).

It then runs inside a READ ONLY transaction with a 5-second time limit and at most
MAX_ROWS rows (see sql_agent.run_safe). Even if a check were bypassed, the database
itself refuses to write in a read-only transaction.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {
    "v_outbreaks", "v_district_year", "v_weather_weekly", "v_risk_latest",
    "districts", "states", "reports", "news_signals",
}
BLOCKED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "file_", "current_setting", "set_config", "query_to_xml",
                             "table_to_xml", "cursor_to_xml", "database_to_xml", "schema_to_xml", "txid_",
                             "inet_", "version", "has_", "copy")
WRITE_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable",
    "Command", "Copy", "Set", "Grant", "Transaction", "Commit", "Rollback", "Into", "Lock", "Use",
    "Pragma", "LoadData", "Analyze", "Kill") if hasattr(exp, n))


class UnsafeSQL(ValueError):
    """The SQL failed a guardrail check."""


def check_sql(sql: str) -> str:
    """Return the cleaned SQL if it is safe to run, otherwise raise UnsafeSQL with the reason."""
    if not sql or not sql.strip():
        raise UnsafeSQL("empty query")
    sql = sql.strip().rstrip(";").strip()
    if len(sql) > 4000:
        raise UnsafeSQL("query too long")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSQL(f"not valid SQL: {str(exc).splitlines()[0][:150]}") from exc
    if len(statements) != 1:
        raise UnsafeSQL("only one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeSQL(f"only SELECT queries are allowed (got {tree.key.upper()})")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise UnsafeSQL(f"{node.key.upper()} is not allowed")
        if isinstance(node, exp.Lock):
            raise UnsafeSQL("row locking is not allowed")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQL(f"schema-qualified table {table.sql()} is not allowed")
        if name in ctes:
            continue
        if not name:
            raise UnsafeSQL("table functions are not allowed")
        if name not in ALLOWED_TABLES:
            raise UnsafeSQL(f"table '{name}' is not allowed; use: {', '.join(sorted(ALLOWED_TABLES))}")

    for func in tree.find_all(exp.Func):
        fname = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
        if fname.startswith(BLOCKED_FUNCTION_PREFIXES):
            raise UnsafeSQL(f"function {fname}() is not allowed")
    return sql
