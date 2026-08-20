"""
database/migrate_data.py
---------------------------
STEP 5D-3 -- migration utility for every SQLite table EXCEPT `users`
(users.id preservation is handled separately by database/migrate_users.py,
Step 5D-2 -- run that first; every table here has a foreign key back to
users.id and this module's audit/plan functions will flag missing
prerequisite user rows rather than guess).

NOT imported by app.py, database/db.py, database/db_router.py, any
backend/*.py, chatbot/prakriti.py, or any page. USE_SUPABASE stays
false regardless of whether this tool exists or has been run. Nothing
here executes automatically on Streamlit startup -- every function
must be explicitly called by a human operator.

DEPENDENCY ORDER -- discovered, not assumed. discover_dependency_graph()
parses the actual `REFERENCES` clauses out of database/schema.sql at
call time (the same technique used to audit this in Step 5D-1/5D-2),
so the migration order below is derived from the real schema, not
hand-typed:

    users (already migrated by migrate_users.py -- a fixed prerequisite,
           not a table this module writes to)
      |
      +-- categories            (no FK -- independent)
      +-- recycling_centres     (no FK -- independent)
      +-- login_attempts        (no FK -- independent)
      +-- complaints            (-> users)
      |     +-- complaint_timeline  (-> complaints, users)
      +-- rewards               (-> users)
      +-- chat_history          (-> users)
      +-- carbon_records        (-> users)
      +-- audit_log             (-> users)
      +-- notifications         (-> users)
            +-- notification_recipients  (-> notifications, users)

dependency_order() topologically sorts this graph so every table is
returned only after every table it references.

CONFLICT POLICY, ID PRESERVATION, TRANSACTION SAFETY: identical
approach to database/migrate_users.py (Step 5D-2) --
  - existing Supabase rows are NEVER overwritten; any id (or, for
    notification_recipients, (notification_id, user_id)) collision is
    skipped and reported, never silently merged;
  - every id is inserted explicitly via `OVERRIDING SYSTEM VALUE`
    (every target table's `id` is a `GENERATED ALWAYS AS IDENTITY`
    column, same as `users`), then the identity sequence is repaired
    afterward;
  - each row is attempted inside its own SAVEPOINT, so one bad row
    never aborts the rest of that table's migration.
See that module's docstring for the fuller rationale -- it isn't
repeated in full here to avoid drift between two copies of the same
explanation.

TYPE CONVERSIONS (documented, not silently guessed) -- see
TABLE_SPECS below for exactly which columns:
  - SQLite INTEGER 0/1 -> Postgres boolean: categories.is_active,
    recycling_centres.is_active, login_attempts.success.
  - SQLite TEXT timestamp ('YYYY-MM-DD HH:MM:SS', naive, actually UTC
    since it comes from SQLite's datetime('now')) -> a real,
    timezone-AWARE (UTC) Python datetime, for every *_at / last_login /
    read_at / expires_at column. This is deliberately more explicit
    than relying on Postgres's implicit text->timestamptz cast on
    INSERT: SQLite's stored text has no timezone marker, and letting
    Postgres guess (via the connection's session TimeZone setting)
    would be correct only by coincidence. Attaching UTC explicitly
    removes that ambiguity. See _normalize_timestamp().

NOT DONE IN THIS STEP (explicitly out of scope, per the task):
  - No SQLite-specific query rewriting in backend/*.py (datetime('now'),
    julianday(), strftime(), INSERT OR IGNORE -- tracked since Step
    5D-1, untouched here).
  - No complaint image/video file migration to Supabase Storage --
    see audit_complaint_image_paths() below, which only *reports* on
    assets/uploads/ references, never moves or copies a file.
  - No change to USE_SUPABASE, database/db.py, or any backend caller.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from database.db import fetch_all as sqlite_fetch_all

logger = logging.getLogger("ecovision.migrate_data")

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Tables this module migrates, in no particular order here --
# dependency_order() below computes the safe order. `users` is
# deliberately excluded: it's a prerequisite migrated by
# database/migrate_users.py, not a table this module writes to.
TARGET_TABLES = (
    "categories", "recycling_centres", "login_attempts",
    "complaints", "rewards", "chat_history", "carbon_records", "audit_log",
    "notifications", "complaint_timeline", "notification_recipients",
)


def discover_dependency_graph(schema_text: str | None = None) -> dict[str, set[str]]:
    """
    Parses `REFERENCES <table>(` out of every `CREATE TABLE IF NOT
    EXISTS <name> (...)` block in database/schema.sql (or a provided
    schema_text, for testing without touching the real file). Returns
    {table_name: {tables it references}}. This is how the migration
    order below is derived -- not hand-typed.
    """
    text = schema_text if schema_text is not None else _SCHEMA_PATH.read_text()
    graph = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", text, re.S):
        refs = set(re.findall(r"REFERENCES (\w+)\(", body)) - {name}  # no self-loops
        graph[name] = refs
    return graph


def dependency_order(tables: tuple[str, ...] = TARGET_TABLES) -> list[str]:
    """
    Topologically sorts `tables` using discover_dependency_graph(), so
    every table is returned only after every other table (within
    `tables`) it references. `users` is treated as an already-satisfied
    prerequisite (not part of the sort, never returned) since it's
    migrated separately. Raises ValueError if the graph has a cycle
    (would indicate a genuine schema problem, not something to silently
    work around).
    """
    graph = discover_dependency_graph()
    remaining = {t: (graph.get(t, set()) - {"users"}) & set(tables) for t in tables}
    ordered = []
    while remaining:
        ready = sorted(t for t, deps in remaining.items() if not deps)
        if not ready:
            raise ValueError(f"Circular or unresolvable table dependency among: {sorted(remaining)}")
        for t in ready:
            ordered.append(t)
            del remaining[t]
        for deps in remaining.values():
            deps.difference_update(ready)
    return ordered


def _normalize_timestamp(value):
    """
    SQLite TEXT timestamp ('YYYY-MM-DD HH:MM:SS', naive-but-actually-UTC,
    from datetime('now')) -> a timezone-aware UTC datetime. None passes
    through as None (many of these columns are nullable, e.g.
    resolved_at, last_login, read_at, expires_at). See module docstring
    for why this is done explicitly rather than left to an implicit cast.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


@dataclass
class TableSpec:
    columns: tuple  # source (== target) column names, in a fixed order
    fk_checks: tuple = ()          # (column, referenced_table) pairs, checked against SQLite itself
    bool_columns: tuple = ()       # 0/1 -> real bool
    timestamp_columns: tuple = ()  # normalized via _normalize_timestamp
    status_column: str | None = None
    allowed_statuses: tuple = ()
    natural_key: tuple = ()        # extra uniqueness check beyond `id` (e.g. notification_recipients)


TABLE_SPECS = {
    "categories": TableSpec(
        columns=("id", "name", "description", "icon", "disposal_guide", "is_active"),
        bool_columns=("is_active",),
    ),
    "recycling_centres": TableSpec(
        columns=("id", "name", "type", "address", "ward", "latitude", "longitude",
                 "contact", "materials_accepted", "is_active"),
        bool_columns=("is_active",),
    ),
    "login_attempts": TableSpec(
        columns=("id", "email", "success", "ip_hint", "created_at"),
        bool_columns=("success",),
        timestamp_columns=("created_at",),
    ),
    "complaints": TableSpec(
        columns=("id", "user_id", "category", "ai_predicted_category", "ai_confidence",
                 "description", "ai_description", "priority", "status", "image_path",
                 "latitude", "longitude", "ward", "address_text", "assigned_officer_id",
                 "assigned_worker", "officer_notes", "created_at", "updated_at", "resolved_at"),
        fk_checks=(("user_id", "users"), ("assigned_officer_id", "users")),
        timestamp_columns=("created_at", "updated_at", "resolved_at"),
        status_column="status",
        allowed_statuses=("Submitted", "Under Review", "Assigned", "In Progress", "Resolved", "Rejected"),
    ),
    "complaint_timeline": TableSpec(
        columns=("id", "complaint_id", "status", "note", "changed_by", "created_at"),
        fk_checks=(("complaint_id", "complaints"), ("changed_by", "users")),
        timestamp_columns=("created_at",),
    ),
    "rewards": TableSpec(
        columns=("id", "user_id", "points", "reason", "created_at"),
        fk_checks=(("user_id", "users"),),
        timestamp_columns=("created_at",),
    ),
    "chat_history": TableSpec(
        columns=("id", "user_id", "session_id", "role", "message", "language", "created_at"),
        fk_checks=(("user_id", "users"),),
        timestamp_columns=("created_at",),
    ),
    "carbon_records": TableSpec(
        columns=("id", "user_id", "transport_kg", "electricity_kg", "plastic_kg",
                 "water_kg", "food_kg", "waste_kg", "total_score", "created_at"),
        fk_checks=(("user_id", "users"),),
        timestamp_columns=("created_at",),
    ),
    "audit_log": TableSpec(
        columns=("id", "user_id", "action", "details", "created_at"),
        fk_checks=(("user_id", "users"),),
        timestamp_columns=("created_at",),
    ),
    "notifications": TableSpec(
        columns=("id", "title", "message", "type", "audience", "priority",
                 "created_by", "created_at", "expires_at"),
        fk_checks=(("created_by", "users"),),
        timestamp_columns=("created_at", "expires_at"),
    ),
    "notification_recipients": TableSpec(
        columns=("id", "notification_id", "user_id", "read_at", "created_at"),
        fk_checks=(("notification_id", "notifications"), ("user_id", "users")),
        timestamp_columns=("read_at", "created_at"),
        natural_key=("notification_id", "user_id"),
    ),
}


@dataclass
class TableAuditReport:
    table: str
    row_count: int = 0
    orphaned_fk_rows: dict = field(default_factory=dict)   # {column: count}
    invalid_status_rows: int = 0
    invalid_status_values: set = field(default_factory=set)
    distinct_language_values: set = field(default_factory=set)  # chat_history only, reporting only


def audit_source_data(tables: tuple = TARGET_TABLES) -> dict[str, TableAuditReport]:
    """
    SQLite-only, read-only audit -- makes no connection to Supabase.
    For every requested table: row count, FK-orphan counts (rows whose
    FK value doesn't exist in the referenced SQLite table -- shouldn't
    normally happen given SQLite's own FK enforcement, but checked as a
    defensive sanity pass rather than assumed), invalid
    enum/status values, and (chat_history only) the distinct `language`
    values actually in use, reported for visibility -- chat_history.language
    has no CHECK constraint in this schema, so nothing here is treated
    as "invalid", only surfaced.
    """
    reports = {}
    for table in tables:
        spec = TABLE_SPECS[table]
        rows = sqlite_fetch_all(f"SELECT * FROM {table}")
        report = TableAuditReport(table=table, row_count=len(rows))

        for col, ref_table in spec.fk_checks:
            ref_ids = {r["id"] for r in sqlite_fetch_all(f"SELECT id FROM {ref_table}")}
            orphans = sum(1 for r in rows if r[col] is not None and r[col] not in ref_ids)
            if orphans:
                report.orphaned_fk_rows[col] = orphans

        if spec.status_column:
            for r in rows:
                val = r[spec.status_column]
                if val not in spec.allowed_statuses:
                    report.invalid_status_rows += 1
                    report.invalid_status_values.add(val)

        if table == "chat_history":
            report.distinct_language_values = {r["language"] for r in rows}

        reports[table] = report
    return reports


def audit_complaint_image_paths() -> dict:
    """
    Read-only. Reports how many complaints.image_path values are set,
    and of those, how many files actually exist on disk under
    assets/uploads/ vs. are missing. Migrates/copies NOTHING -- Supabase
    Storage migration is explicitly a separate, later step (per this
    step's instructions, item J).
    """
    rows = sqlite_fetch_all("SELECT id, image_path FROM complaints WHERE image_path IS NOT NULL AND image_path != ''")
    existing, missing = [], []
    for r in rows:
        (existing if Path(r["image_path"]).is_file() else missing).append(r["id"])
    return {
        "total_complaints_with_image_path": len(rows),
        "files_found_on_disk": len(existing),
        "files_missing": len(missing),
        "missing_complaint_ids": missing,
    }


def _row_to_params(table: str, row: dict) -> tuple:
    spec = TABLE_SPECS[table]
    values = []
    for col in spec.columns:
        v = row[col]
        if col in spec.bool_columns:
            v = bool(v)
        elif col in spec.timestamp_columns:
            v = _normalize_timestamp(v)
        values.append(v)
    return tuple(values)


def _insert_sql(table: str) -> str:
    cols = TABLE_SPECS[table].columns
    return (f"INSERT INTO {table} (" + ", ".join(cols) + ") "
            f"OVERRIDING SYSTEM VALUE VALUES (" + ", ".join(["%s"] * len(cols)) + ")")


@dataclass
class RowMigrationResult:
    table: str
    row_id: int
    action: str  # "insert" | "would_insert" | "skip" | "would_skip" | "inserted" | "error"
    reason: str = ""


def _plan_table(table: str, sqlite_rows: list[dict], existing_ids: set, existing_natural_keys: set) -> list[RowMigrationResult]:
    """Pure function -- no I/O. Same conflict policy as
    database/migrate_users.py: an existing `id` is skipped outright; for
    tables with a natural_key (currently just notification_recipients'
    (notification_id, user_id) UNIQUE constraint), a colliding natural
    key under a *different* id is also skipped, since inserting it would
    violate that constraint even though the id itself is free."""
    spec = TABLE_SPECS[table]
    plan = []
    for row in sqlite_rows:
        if row["id"] in existing_ids:
            plan.append(RowMigrationResult(table, row["id"], "skip",
                f"id {row['id']} already exists in Supabase {table}"))
            continue
        if spec.natural_key:
            key = tuple(row[c] for c in spec.natural_key)
            if key in existing_natural_keys:
                plan.append(RowMigrationResult(table, row["id"], "skip",
                    f"{dict(zip(spec.natural_key, key))} already exists in Supabase {table} under a different id"))
                continue
        plan.append(RowMigrationResult(table, row["id"], "insert", "no conflict"))
    return plan


def plan_migration(tables: tuple = TARGET_TABLES) -> dict[str, list[RowMigrationResult]]:
    """
    dry_run planning across every requested table, in dependency order.
    Connects to Supabase READ-ONLY (only SELECTs id / natural-key
    columns) -- makes no writes. Raises
    database.db_supabase.SupabaseAdapterError if Supabase isn't
    configured/reachable (no silent fallback, consistent with every
    other Step 5 module).
    """
    from database.db_supabase import get_connection

    ordered = dependency_order(tables)
    result = {}
    with get_connection() as conn:
        for table in ordered:
            spec = TABLE_SPECS[table]
            sqlite_rows = sqlite_fetch_all(f"SELECT * FROM {table}")
            existing_id_rows = conn.execute(f"SELECT id FROM {table}").fetchall()
            existing_ids = {r["id"] for r in existing_id_rows}
            existing_natural_keys = set()
            if spec.natural_key:
                cols_sql = ", ".join(spec.natural_key)
                nk_rows = conn.execute(f"SELECT {cols_sql} FROM {table}").fetchall()
                existing_natural_keys = {tuple(r[c] for c in spec.natural_key) for r in nk_rows}
            plan = _plan_table(table, sqlite_rows, existing_ids, existing_natural_keys)
            for p in plan:
                p.action = "would_insert" if p.action == "insert" else "would_skip"
            result[table] = plan
    return result


def migrate_table(table: str, dry_run: bool = True) -> list[RowMigrationResult]:
    """
    Migrates ONE table. dry_run=True (default): plan only, no writes
    (delegates to plan_migration() for just this table). dry_run=False:
    actually inserts, one row per SAVEPOINT (a single row's failure
    never aborts the rest of this table's migration), then repairs
    that table's identity sequence if anything was inserted.
    """
    from database.db_supabase import get_connection

    if dry_run:
        return plan_migration((table,))[table]

    spec = TABLE_SPECS[table]
    sqlite_rows = sqlite_fetch_all(f"SELECT * FROM {table}")
    by_id = {r["id"]: r for r in sqlite_rows}

    with get_connection() as conn:
        existing_ids = {r["id"] for r in conn.execute(f"SELECT id FROM {table}").fetchall()}
        existing_natural_keys = set()
        if spec.natural_key:
            cols_sql = ", ".join(spec.natural_key)
            nk_rows = conn.execute(f"SELECT {cols_sql} FROM {table}").fetchall()
            existing_natural_keys = {tuple(r[c] for c in spec.natural_key) for r in nk_rows}

        plan = _plan_table(table, sqlite_rows, existing_ids, existing_natural_keys)
        insert_sql = _insert_sql(table)
        any_inserted = False

        for p in plan:
            if p.action != "insert":
                continue
            row = by_id[p.row_id]
            try:
                conn.execute("SAVEPOINT row_migration")
                conn.execute(insert_sql, _row_to_params(table, row))
                conn.execute("RELEASE SAVEPOINT row_migration")
                p.action = "inserted"
                any_inserted = True
            except Exception as e:
                conn.execute("ROLLBACK TO SAVEPOINT row_migration")
                p.action = "error"
                p.reason = str(e)
                logger.warning("Row migration failed for %s id=%s: %s", table, row["id"], e)

        if any_inserted:
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
            )

        return plan


def migrate_all(dry_run: bool = True) -> dict[str, list[RowMigrationResult]]:
    """
    Runs migrate_table() for every table in TARGET_TABLES, in
    dependency_order(). Each table gets its own connection/transaction
    (via migrate_table -> get_connection()), so a failure partway
    through one table never touches a table migrated before it, and a
    later table's migration can safely be re-run (idempotent -- rows
    already present are skipped, not re-inserted or duplicated).
    """
    ordered = dependency_order()
    return {table: migrate_table(table, dry_run=dry_run) for table in ordered}


def compare_counts(table: str) -> dict:
    """Row-count comparison for one table, SQLite vs. Supabase.
    Requires a live Supabase connection -- raises SupabaseAdapterError
    if unconfigured, same as every other read here."""
    from database.db_supabase import get_connection
    sqlite_count = sqlite_fetch_all(f"SELECT COUNT(*) as c FROM {table}")[0]["c"]
    with get_connection() as conn:
        supabase_count = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()["c"]
    return {"table": table, "sqlite_count": sqlite_count, "supabase_count": supabase_count,
            "match": sqlite_count == supabase_count}


def compare_id_sets(table: str) -> dict:
    """Which ids are only in SQLite, only in Supabase, or present in
    both, for one table. Requires a live Supabase connection."""
    from database.db_supabase import get_connection
    sqlite_ids = {r["id"] for r in sqlite_fetch_all(f"SELECT id FROM {table}")}
    with get_connection() as conn:
        supabase_ids = {r["id"] for r in conn.execute(f"SELECT id FROM {table}").fetchall()}
    return {
        "table": table,
        "only_in_sqlite": sorted(sqlite_ids - supabase_ids),
        "only_in_supabase": sorted(supabase_ids - sqlite_ids),
        "matched": sorted(sqlite_ids & supabase_ids),
    }


def verify_migration(tables: tuple = TARGET_TABLES) -> dict:
    """Runs compare_counts() + compare_id_sets() for every table.
    Requires a live Supabase connection for every table checked."""
    ordered = dependency_order(tables)
    return {t: {"counts": compare_counts(t), "ids": compare_id_sets(t)} for t in ordered}
