"""
database/sql_dialect.py
--------------------------
STEP 5D-1 -- SQL dialect compatibility helpers. NOT imported by
backend/auth.py, backend/complaints.py, backend/analytics.py,
backend/notifications.py, chatbot/prakriti.py, database/db.py, or any
page yet. Adopting these (rewriting those call sites to use them) is a
later, feature-specific step -- this module only prepares and tests the
translations in isolation, per Step 5D-1's scope.

WHY THIS EXISTS INSTEAD OF STRING-SUBSTITUTION IN THE ADAPTER:
database/db_supabase.py (Step 5C) deliberately does NOT try to rewrite
SQLite-specific SQL fragments (datetime('now'), julianday(), strftime(),
INSERT OR IGNORE) inside arbitrary query strings -- regex-based rewriting
of embedded SQL is fragile and easy to get subtly wrong. Instead, this
module gives call sites an explicit choice: ask for the right SQL
fragment for a given backend, in code, where it's easy to review and
test. A future step would change (for example)

    execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (uid,))

to

    execute(f"UPDATE users SET last_login={now_expr(backend)} WHERE id=?", (uid,))

-- a small, obviously-correct, per-call-site diff, not a blanket find/
replace across the codebase.

Every function here is pure (no I/O, no database connection, no global
state) and takes an explicit `backend` argument ("sqlite" or
"postgres") rather than auto-detecting it -- this keeps the module
trivially unit-testable and means importing it never has any side
effect or coupling to database/db_router.py.
"""
from __future__ import annotations

SQLITE = "sqlite"
POSTGRES = "postgres"
_VALID_BACKENDS = (SQLITE, POSTGRES)


def _check_backend(backend: str) -> None:
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"backend must be one of {_VALID_BACKENDS}, got {backend!r}")


def current_dialect() -> str:
    """
    Convenience for real call sites -- NOT used by this module's own
    pure functions above (they always take an explicit `backend`
    argument, which is what keeps them independently unit-testable
    without any app/router state). Returns SQLITE or POSTGRES based on
    database.db_router.active_backend() ("sqlite"/"supabase" -> this
    module's "sqlite"/"postgres" naming, since db_router names its two
    backends after the *products* while this module names its two
    dialects after the *SQL flavor* -- Supabase's flavor is Postgres).
    Imports db_router lazily so importing sql_dialect.py itself still
    has zero side effects or dependency on db_router -- only calling
    this specific function does.
    """
    from database.db_router import active_backend
    return POSTGRES if active_backend() == "supabase" else SQLITE


def now_expr(backend: str = SQLITE) -> str:
    """
    SQL fragment for "the current timestamp", for use directly inside a
    query string (e.g. an UPDATE ... SET col=<this>).
        sqlite:   datetime('now')
        postgres: now()
    Used today (unchanged) in: backend/auth.py (last_login updates),
    backend/complaints.py (updated_at/resolved_at), backend/notifications.py
    (read_at).
    """
    _check_backend(backend)
    return "datetime('now')" if backend == SQLITE else "now()"


def age_hours_expr(start_col: str, end_col: str, backend: str = SQLITE) -> str:
    """
    SQL fragment computing the age in hours between two timestamp
    columns (end - start), as a plain numeric expression.
        sqlite:   (julianday(end) - julianday(start)) * 24
        postgres: EXTRACT(EPOCH FROM (end - start)) / 3600
    Used today (unchanged) in: backend/analytics.py::kpi_summary()'s
    avg_resolution_hours (resolved_at - created_at).
    """
    _check_backend(backend)
    if backend == SQLITE:
        return f"(julianday({end_col}) - julianday({start_col})) * 24"
    return f"EXTRACT(EPOCH FROM ({end_col} - {start_col})) / 3600"


def month_trunc_expr(col: str, backend: str = SQLITE) -> str:
    """
    SQL fragment formatting a timestamp column as "YYYY-MM".
        sqlite:   strftime('%Y-%m', col)
        postgres: to_char(col, 'YYYY-MM')
    Used today (unchanged) in:
    backend/analytics.py::complaints_monthly_trend().
    """
    _check_backend(backend)
    if backend == SQLITE:
        return f"strftime('%Y-%m', {col})"
    return f"to_char({col}, 'YYYY-MM')"


def date_expr(col: str, backend: str = SQLITE) -> str:
    """
    SQL fragment truncating a timestamp column to a plain date.
        sqlite:   date(col)
        postgres: col::date
    Used today (unchanged) in:
    backend/analytics.py::complaints_daily_trend().
    """
    _check_backend(backend)
    return f"date({col})" if backend == SQLITE else f"{col}::date"


def now_minus_days_expr(days: int, backend: str = SQLITE) -> str:
    """
    SQL fragment for "now, minus N days" -- used as a WHERE-clause
    lower bound.
        sqlite:   datetime('now', '-N days')
        postgres: now() - interval 'N days'
    `days` is always an internal int (function-default parameter or
    hardcoded caller value in this codebase), never raw user input, so
    inlining it is safe -- still validated as an int here defensively.
    Used today (unchanged) in:
    backend/analytics.py::complaints_daily_trend().
    """
    _check_backend(backend)
    days = int(days)  # defensive: reject anything that isn't a plain integer
    if backend == SQLITE:
        return f"datetime('now', '-{days} days')"
    return f"now() - interval '{days} days'"


def insert_ignore_sql(table: str, columns: list[str], conflict_columns: list[str], backend: str = SQLITE) -> str:
    """
    Full INSERT statement template (with "?" placeholders -- the same
    placeholder style regardless of backend; database/db_supabase.py
    already translates "?" -> "%s" uniformly for the postgres path, so
    this function doesn't need to know about that).
        sqlite:   INSERT OR IGNORE INTO t (a,b) VALUES (?,?)
        postgres: INSERT INTO t (a,b) VALUES (?,?) ON CONFLICT (a) DO NOTHING
    Used today (unchanged) in: database/db.py::_seed_categories(),
    backend/notifications.py::send_notification() (recipient fan-out),
    pages/8_🛠️_Admin_Dashboard.py (add-category form).
    """
    _check_backend(backend)
    cols_sql = ", ".join(columns)
    placeholders_sql = ", ".join("?" for _ in columns)
    if backend == SQLITE:
        return f"INSERT OR IGNORE INTO {table} ({cols_sql}) VALUES ({placeholders_sql})"
    conflict_sql = ", ".join(conflict_columns)
    return (f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders_sql}) "
            f"ON CONFLICT ({conflict_sql}) DO NOTHING")


def failed_attempts_since_param(minutes: int, backend: str = SQLITE):
    """
    Returns the correctly-typed parameter VALUE (not a SQL fragment) to
    bind against a "created_at >= ?" comparison, for "the last N
    minutes". This is a Step 5D-1 addition beyond Step 5C's list --
    found while re-auditing every execute()/fetch_one() call site (see
    the Step 5D-1 report): backend/auth.py::_recent_failed_attempts()
    currently formats a Python datetime as a fixed-format string
    ('%Y-%m-%d %H:%M:%S') and compares it against `login_attempts.
    created_at` as plain text. That works in SQLite (created_at is
    itself stored as text), but under Postgres, created_at is a native
    timestamptz column -- comparing it to a plain string parameter is
    unreliable without an explicit cast, whereas psycopg can adapt a
    real Python datetime object to timestamptz directly and correctly.
        sqlite:   a formatted string, exactly matching today's behavior
        postgres: a real (naive UTC) datetime object
    Used today (unchanged) in: backend/auth.py::_recent_failed_attempts().
    """
    _check_backend(backend)
    from datetime import datetime, timedelta
    since = datetime.utcnow() - timedelta(minutes=minutes)
    if backend == SQLITE:
        return since.strftime("%Y-%m-%d %H:%M:%S")
    return since
