"""
database/verify_migration.py
--------------------------------
STEP 5D-4 -- verification/readiness layer for the SQLite -> Supabase
migration. NOT imported by app.py, database/db.py, database/db_router.py,
backend/auth.py, or any page. USE_SUPABASE stays false regardless.
Every function here is READ-ONLY on both databases -- nothing in this
module ever writes, updates, deletes, or migrates a single row. It only
compares what database/migrate_users.py (Step 5D-2) and
database/migrate_data.py (Step 5D-3) have already written to Supabase
against the SQLite source of truth.

REUSES rather than duplicates: table row-count and id-set comparisons
call database.migrate_data.compare_counts() / compare_id_sets()
directly -- those already exist and are already tested (Step 5D-3).
This module adds the checks that weren't needed for the migration tools
themselves: per-column data-integrity/type verification, FK integrity
on the SUPABASE side post-migration (distinct from migrate_data's
SQLite-side pre-migration orphan audit), and a single PASS/FAIL report
that aggregates all of it per table and overall.

`users` IS covered here (see ALL_TABLES) even though it's migrated by a
separate tool (migrate_users.py) -- compare_counts()/compare_id_sets()
are generic over any table name, so reusing them for `users` needed no
new code, and this step's task explicitly requires verifying "users and
all dependent foreign keys."

NO SILENT FALLBACK: every function that touches Supabase raises
database.db_supabase.SupabaseAdapterError (via database.migrate_data's
functions, which already raise it) if Supabase isn't configured or
reachable -- consistent with every other Step 5 module. dry_run mode
(see verify_all()) exists to let an operator sanity-check configuration
before running the (still read-only, but heavier) full comparison --
it is not a way to get a fake "PASS" without a real connection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from database.db import fetch_all as sqlite_fetch_all
from database.migrate_data import (
    TABLE_SPECS,
    TARGET_TABLES,
    dependency_order,
    compare_counts,
    compare_id_sets,
)

logger = logging.getLogger("ecovision.verify_migration")

# users first (everything else depends on it), then the Step 5D-3
# tables in their already-discovered, dependency-safe order.
ALL_TABLES = ("users",) + tuple(dependency_order(TARGET_TABLES))


@dataclass
class TableVerificationReport:
    table: str
    status: str = "PASS"  # "PASS" | "FAIL" | "SKIPPED"
    counts: dict = field(default_factory=dict)
    ids: dict = field(default_factory=dict)
    foreign_key_violations: dict = field(default_factory=dict)   # {column: [orphan ids]}
    invalid_status_rows: list = field(default_factory=list)      # [id, ...] (complaints only)
    type_issues: list = field(default_factory=list)              # human-readable strings
    notes: list = field(default_factory=list)


def verify_foreign_keys(table: str) -> dict:
    """
    Checks every FK column declared for `table` in
    database.migrate_data.TABLE_SPECS -- for each non-null FK value in
    Supabase's copy of `table`, confirms the referenced row actually
    exists in Supabase's copy of the referenced table. This is
    deliberately the SUPABASE side (post-migration integrity), distinct
    from migrate_data.audit_source_data()'s SQLite-side pre-migration
    orphan check. Returns {column: [orphaned row ids]} -- empty dict
    means no violations found.

    Together with ALL_TABLES covering every table with a `REFERENCES
    users(id)` (complaints x2, complaint_timeline, rewards,
    chat_history, carbon_records, audit_log, notifications,
    notification_recipients -- 9 references total, matching the count
    established in the Step 5D-2/5D-3 audits), running this for every
    table in ALL_TABLES verifies all of them.
    """
    from database.db_supabase import get_connection

    spec = TABLE_SPECS.get(table)
    if not spec or not spec.fk_checks:
        return {}

    violations = {}
    with get_connection() as conn:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        for col, ref_table in spec.fk_checks:
            ref_ids = {r["id"] for r in conn.execute(f"SELECT id FROM {ref_table}").fetchall()}
            orphans = [r["id"] for r in rows if r.get(col) is not None and r[col] not in ref_ids]
            if orphans:
                violations[col] = orphans
    return violations


def verify_status_values(table: str = "complaints") -> list:
    """Returns the list of Supabase row ids whose status column is
    outside TABLE_SPECS[table].allowed_statuses. Empty list = all valid."""
    from database.db_supabase import get_connection

    spec = TABLE_SPECS.get(table)
    if not spec or not spec.status_column:
        return []
    with get_connection() as conn:
        rows = conn.execute(f"SELECT id, {spec.status_column} FROM {table}").fetchall()
    return [r["id"] for r in rows if r[spec.status_column] not in spec.allowed_statuses]


def verify_chat_language() -> set:
    """Distinct chat_history.language values present in Supabase --
    reporting only (no CHECK constraint restricts this column in either
    database), matching the equivalent SQLite-side check in
    migrate_data.audit_source_data()."""
    from database.db_supabase import get_connection
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT language FROM chat_history").fetchall()
    return {r["language"] for r in rows}


def verify_type_conversions(table: str) -> list:
    """
    Confirms bool_columns actually come back as Python bool (not 0/1)
    and timestamp_columns actually come back as real datetime objects
    (not strings) when read from Supabase -- i.e. that the Step 5D-3
    type conversions (documented in migrate_data.py) took effect and
    stuck, not just that the migration code intended them to. Returns a
    list of human-readable issue strings; empty = all good.
    """
    from database.db_supabase import get_connection

    spec = TABLE_SPECS.get(table)
    if not spec:
        return []
    issues = []
    if not spec.bool_columns and not spec.timestamp_columns:
        return issues

    with get_connection() as conn:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()

    for row in rows:
        for col in spec.bool_columns:
            val = row.get(col)
            if val is not None and not isinstance(val, bool):
                issues.append(f"{table}.{col} (id={row['id']}): expected bool, got {type(val).__name__}")
        for col in spec.timestamp_columns:
            val = row.get(col)
            if val is not None and not isinstance(val, datetime):
                issues.append(f"{table}.{col} (id={row['id']}): expected datetime, got {type(val).__name__}")
    return issues


def verify_image_paths() -> dict:
    """
    Row-by-row comparison of complaints.image_path between SQLite and
    Supabase, by id -- this is a value-preservation check (did the
    string survive the migration unchanged?), NOT a file-existence
    check (that's migrate_data.audit_complaint_image_paths(), which
    only touches SQLite and never Supabase, and is unaffected by this
    function). No file is read, copied, or migrated to Storage here or
    anywhere in this module.
    """
    from database.db_supabase import get_connection

    sqlite_rows = {r["id"]: r["image_path"] for r in sqlite_fetch_all("SELECT id, image_path FROM complaints")}
    with get_connection() as conn:
        supabase_rows = {r["id"]: r["image_path"] for r in conn.execute("SELECT id, image_path FROM complaints").fetchall()}

    mismatches = []
    for cid, sqlite_path in sqlite_rows.items():
        if cid in supabase_rows and supabase_rows[cid] != sqlite_path:
            mismatches.append({"id": cid, "sqlite": sqlite_path, "supabase": supabase_rows[cid]})
    return {"checked": len(sqlite_rows), "mismatches": mismatches}


def verify_table(table: str) -> TableVerificationReport:
    """
    Full read-only verification of one table: row counts, id sets
    (reusing database.migrate_data's already-tested functions), FK
    integrity, plus table-specific checks (status values for
    `complaints`, language values + image_path for the relevant
    tables), and a type-conversion sanity check for every bool/
    timestamp column declared in TABLE_SPECS. `users` has no
    TABLE_SPECS entry (it's migrate_users.py's table, not
    migrate_data.py's) so only counts/ids apply to it here.
    """
    report = TableVerificationReport(table=table)

    report.counts = compare_counts(table)
    report.ids = compare_id_sets(table)
    if not report.counts["match"]:
        report.status = "FAIL"
        report.notes.append(f"row count mismatch: sqlite={report.counts['sqlite_count']} "
                             f"supabase={report.counts['supabase_count']}")
    if report.ids["only_in_sqlite"]:
        report.status = "FAIL"
        report.notes.append(f"{len(report.ids['only_in_sqlite'])} id(s) missing from Supabase "
                             f"(present in SQLite only): {report.ids['only_in_sqlite'][:10]}"
                             + (" ..." if len(report.ids["only_in_sqlite"]) > 10 else ""))
    if report.ids["only_in_supabase"]:
        report.status = "FAIL"
        report.notes.append(f"{len(report.ids['only_in_supabase'])} unexpected id(s) in Supabase "
                             f"(not present in SQLite): {report.ids['only_in_supabase'][:10]}"
                             + (" ..." if len(report.ids["only_in_supabase"]) > 10 else ""))

    if table in TABLE_SPECS:
        fk_violations = verify_foreign_keys(table)
        if fk_violations:
            report.foreign_key_violations = fk_violations
            report.status = "FAIL"
            report.notes.append(f"foreign key violations: {fk_violations}")

        if TABLE_SPECS[table].status_column:
            bad_status_ids = verify_status_values(table)
            if bad_status_ids:
                report.invalid_status_rows = bad_status_ids
                report.status = "FAIL"
                report.notes.append(f"{len(bad_status_ids)} row(s) with an invalid status value: {bad_status_ids}")

        type_issues = verify_type_conversions(table)
        if type_issues:
            report.type_issues = type_issues
            report.status = "FAIL"
            report.notes.append(f"{len(type_issues)} type-conversion issue(s)")

    if table == "chat_history":
        report.notes.append(f"language values in use: {sorted(verify_chat_language())}")

    if table == "complaints":
        img = verify_image_paths()
        if img["mismatches"]:
            report.status = "FAIL"
            report.notes.append(f"{len(img['mismatches'])} image_path value(s) changed during migration")
        else:
            report.notes.append(f"image_path values verified unchanged for {img['checked']} row(s)")

    return report


def verify_all(dry_run: bool = True) -> dict:
    """
    dry_run=True (default -- the safe, fast choice): only confirms
    Supabase is configured and reachable (via a single trivial query),
    then returns without running any per-table comparison. Useful as a
    quick readiness check before committing to the heavier full run.
    Still makes no writes either way -- "dry_run" here means "skip the
    detailed comparison", not "skip safety", since NOTHING in this
    module ever writes.

    dry_run=False: runs verify_table() for every table in ALL_TABLES
    (users first, then the Step 5D-3 tables in dependency order) and
    returns {"overall": "PASS"|"FAIL", "tables": {table: TableVerificationReport}}.

    Raises database.db_supabase.SupabaseAdapterError if Supabase isn't
    configured or reachable -- never silently reports PASS or FAIL
    without a real connection.
    """
    from database.db_supabase import get_connection

    with get_connection() as conn:
        conn.execute("SELECT 1")  # connectivity check -- also what makes an unconfigured/
        # unreachable Supabase fail loudly here rather than partway through table 6 of 12.

    if dry_run:
        return {"overall": "NOT RUN (dry_run=True) -- Supabase reachable, no tables compared",
                "tables": {}}

    reports = {table: verify_table(table) for table in ALL_TABLES}
    overall = "PASS" if all(r.status == "PASS" for r in reports.values()) else "FAIL"
    return {"overall": overall, "tables": reports}
