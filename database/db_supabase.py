"""
database/db_supabase.py
--------------------------
STEP 5C -- Supabase database ADAPTER. Not imported by database/db.py,
backend/*.py, chatbot/prakriti.py, or any page yet. database/db.py's
SQLite implementation remains the application's only active backend.

This module implements the same four-function surface as database/db.py
(execute, fetch_one, fetch_all, get_connection) so that a future step
can route to this module instead of database/db.py with minimal call-
site changes -- see database/db_router.py, which is the (also inert)
dispatcher that will eventually make that choice based on
config.settings.USE_SUPABASE.

WHY A RAW POSTGRES DRIVER (psycopg) INSTEAD OF supabase-py:
database/supabase_client.py (Step 5A) wraps supabase-py, which talks to
Supabase's REST API (PostgREST) -- it does not support executing
arbitrary raw parameterized SQL strings, which is exactly what this
codebase's execute()/fetch_one()/fetch_all() contract requires (every
call site in backend/*.py, chatbot/prakriti.py, and 4 page files passes
a raw SQL string + params tuple). Supabase also exposes a direct
Postgres connection (Project Settings -> Database -> Connection
string) for exactly this use case -- that's what SUPABASE_DB_URL
(config/settings.py) is, and what this module connects to via psycopg.

WHAT THIS ADAPTER DOES:
- Translates SQLite's "?" positional placeholders to Postgres's "%s"
  (mechanical, safe for every current call site -- see
  _translate_placeholders()'s docstring for the one caveat).
- Emulates SQLite's cur.lastrowid for INSERTs by appending
  "RETURNING id" when a query is an INSERT without one already (every
  current INSERT-then-use-the-id call site inserts into a table with an
  integer "id" primary key -- see the Step 5C caller inventory).
- Returns rows as plain dicts, matching database/db.py's _row_factory,
  so callers see identical shapes either way.

WHAT THIS ADAPTER DELIBERATELY DOES NOT DO:
- It does NOT rewrite SQLite-specific SQL *inside* query strings
  (datetime('now'), julianday(), strftime(), "INSERT OR IGNORE"), and
  does NOT re-type a Python-formatted datetime string into a native
  timestamp for backend/auth.py::_recent_failed_attempts()'s
  "created_at >= ?" comparison. Those exist in backend/auth.py,
  backend/complaints.py, backend/analytics.py, backend/notifications.py,
  database/db.py, and pages/8. Postgres has different equivalents
  (now(), EXTRACT(EPOCH...), to_char(...), "INSERT ... ON CONFLICT DO
  NOTHING", a real datetime object instead of a formatted string) --
  see database/sql_dialect.py (Step 5D-1) for those, as pure, unit-
  tested, per-call-site helper functions -- but rewriting query strings
  automatically via string substitution here would be fragile and
  give false confidence -- they need to be fixed at each call site, as
  a deliberate, reviewable change. See the Step 5C report for the full
  list. Running one of these query strings through this adapter as-is
  will raise a clear Postgres error (undefined function), not silently
  misbehave.
- It does NOT run database/schema.sql (that file is SQLite dialect).
  Schema provisioning for Supabase is the already-documented
  database/migrations/*.sql workflow (see database/README.md) -- run
  once, manually, via the Supabase SQL Editor, not on every app boot.

NO SILENT FALLBACK: every function here raises a clear exception on
misconfiguration or connection failure. Nothing in this module ever
falls back to SQLite -- that would hide exactly the kind of migration
problem this adapter needs to surface loudly.
"""
from __future__ import annotations

import re
import logging
from contextlib import contextmanager

from config import settings

logger = logging.getLogger("ecovision.db_supabase")

try:
    import psycopg
    from psycopg.rows import dict_row
    _PSYCOPG_AVAILABLE = True
except ImportError:
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore
    _PSYCOPG_AVAILABLE = False


class SupabaseAdapterError(RuntimeError):
    """
    Raised for any Supabase adapter configuration or connection problem.
    Deliberately a distinct, clearly-named exception type (rather than a
    generic RuntimeError) so a future caller can catch it specifically
    if needed, and so it's unmistakable in logs/tracebacks that this is
    a Supabase-path failure, not a SQLite one.
    """


def is_sdk_available() -> bool:
    """True once the `psycopg` PyPI package is importable."""
    return _PSYCOPG_AVAILABLE


def _translate_placeholders(query: str) -> str:
    """
    SQLite's "?" -> Postgres's "%s". Safe for every current call site in
    this codebase (checked as part of the Step 5C caller inventory: no
    existing query string contains a literal "?" inside a quoted string
    literal or as a JSON/text operator). A query that legitimately needs
    a literal "?" character would need to escape it before this
    translation -- not a concern for any query in this codebase today.
    """
    return query.replace("?", "%s")


_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO", re.IGNORECASE)
_RETURNING_RE = re.compile(r"\bRETURNING\b", re.IGNORECASE)


@contextmanager
def get_connection():
    """
    Yields a psycopg connection (dict-row cursor factory, so
    `conn.execute(...).fetchone()` returns a plain dict like
    database/db.py's SQLite rows do). Raises SupabaseAdapterError
    immediately -- never returns None, never falls back -- if psycopg
    isn't installed or SUPABASE_DB_URL isn't configured. Commits on
    clean exit, rolls back on exception, matching database/db.py's
    get_connection() contract exactly.
    """
    if not _PSYCOPG_AVAILABLE:
        raise SupabaseAdapterError(
            "The `psycopg` package is not installed (see requirements.txt) -- "
            "cannot use the Supabase database adapter."
        )
    if not settings.is_supabase_db_configured():
        raise SupabaseAdapterError(
            "SUPABASE_DB_URL is not set (or is still a placeholder value). "
            "Set it in .env or .streamlit/secrets.toml -- see .env.example."
        )

    try:
        conn = psycopg.connect(settings.SUPABASE_DB_URL, row_factory=dict_row, autocommit=False)
    except Exception as e:
        raise SupabaseAdapterError(f"Could not connect to Supabase Postgres: {e}") from e

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Supabase adapter: transaction rolled back")
        raise
    finally:
        conn.close()


def execute(query: str, params: tuple = ()):
    """
    Same contract as database/db.py::execute() -- returns an id for an
    INSERT, matching SQLite's cur.lastrowid, via an auto-appended
    "RETURNING id" (see module docstring). For UPDATE/DELETE/anything
    else, returns cur.rowcount instead -- no current caller uses
    execute()'s return value for non-INSERT statements (verified as
    part of the Step 5C caller inventory), so this is a safe, documented
    stand-in rather than a silent None.
    """
    pg_query = _translate_placeholders(query)
    is_insert = bool(_INSERT_RE.match(query)) and not _RETURNING_RE.search(query)
    if is_insert:
        pg_query = pg_query.rstrip().rstrip(";") + " RETURNING id"

    with get_connection() as conn:
        cur = conn.execute(pg_query, params)
        if is_insert:
            row = cur.fetchone()
            return row["id"] if row else None
        return cur.rowcount


def fetch_one(query: str, params: tuple = ()):
    with get_connection() as conn:
        cur = conn.execute(_translate_placeholders(query), params)
        return cur.fetchone()


def fetch_all(query: str, params: tuple = ()):
    with get_connection() as conn:
        cur = conn.execute(_translate_placeholders(query), params)
        return cur.fetchall()


def init_db():
    """
    Deliberately does NOT run database/schema.sql (SQLite dialect --
    would fail against Postgres) or attempt any schema creation. Just
    verifies the adapter can actually reach the configured database, so
    a misconfigured Supabase setup fails loudly and immediately instead
    of on the app's first real query. Schema must already exist, via
    database/migrations/*.sql (see database/README.md).
    """
    with get_connection() as conn:
        conn.execute("SELECT 1")
    logger.info("Supabase adapter: connectivity verified. Schema is NOT auto-created -- "
                "run database/migrations/*.sql via the Supabase SQL Editor first.")


def health_check() -> dict:
    """
    Non-raising diagnostic, mirroring database/supabase_client.py's
    health_check() but for the direct-Postgres path specifically.
    Never raises -- unlike execute()/fetch_one()/fetch_all()/init_db()
    above, which raise loudly by design (see module docstring's "NO
    SILENT FALLBACK" note). Use this for status displays; use the
    functions above for actual data access.
    """
    result = {
        "driver_installed": _PSYCOPG_AVAILABLE,
        "configured": settings.is_supabase_db_configured(),
        "reachable": False,
        "detail": "",
    }
    if not _PSYCOPG_AVAILABLE:
        result["detail"] = "The `psycopg` package is not installed (see requirements.txt)."
        return result
    if not result["configured"]:
        result["detail"] = "SUPABASE_DB_URL is not set (or still a placeholder value)."
        return result
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        result["reachable"] = True
        result["detail"] = "Reached the configured Supabase Postgres database."
    except Exception as e:
        result["detail"] = f"Connection failed: {e}"
    return result
