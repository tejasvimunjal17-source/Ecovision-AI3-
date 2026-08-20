"""
database/preflight_5g.py
----------------------------
STEP 5G-1 -- single-command preflight check. NOT imported by the
running application. Never writes to SQLite or Supabase -- every
Supabase-side check here is a `SELECT COUNT(*)` or an
information_schema lookup, nothing else.

Reuses rather than duplicates: database.db_supabase (SDK/config checks,
health_check(), get_connection()), database.migrate_data (TARGET_TABLES,
dependency_order()), database.db (SQLite source counts). This file only
adds the one thing that didn't already exist: a single report combining
"does this table exist in Supabase yet, and how many rows are already
there" against "how many rows does SQLite have", for every table, in
one call -- so a human can eyeball readiness before running any
migration tool.

Usage (see database/RUNBOOK_5G.md for the full sequence):
    python3 -c "from database.preflight_5g import run_preflight; \\
                import json; print(json.dumps(run_preflight(), indent=2, default=str))"
"""
from __future__ import annotations

from config import settings
from database.db import fetch_all as sqlite_fetch_all
from database.migrate_data import TARGET_TABLES, dependency_order

ALL_TABLES = ("users",) + tuple(dependency_order(TARGET_TABLES))


def run_preflight() -> dict:
    """
    Never raises for a missing/incomplete configuration -- that's
    exactly the condition this function exists to report clearly.
    Only reports presence/state, never a secret value.
    """
    report = {
        "sdk_installed": None,
        "configured": None,
        "reachable": None,
        "detail": "",
        "tables": {},   # {table: {"sqlite_count": int, "supabase_exists": bool, "supabase_count": int|None}}
        "warnings": [],
        "ready_for_dry_run": False,
    }

    from database.db_supabase import is_sdk_available
    report["sdk_installed"] = is_sdk_available()
    report["configured"] = settings.is_supabase_db_configured()

    if not report["sdk_installed"]:
        report["detail"] = "The `psycopg` package is not installed -- cannot connect. See requirements.txt."
        return report
    if not report["configured"]:
        report["detail"] = "SUPABASE_DB_URL is not set (or is still a placeholder value)."
        return report

    from database.db_supabase import health_check, get_connection
    hc = health_check()
    report["reachable"] = hc["reachable"]
    report["detail"] = hc["detail"]
    if not hc["reachable"]:
        return report

    # Reachable -- now check schema/table state and collect counts.
    # Every query below is a SELECT; nothing here writes.
    with get_connection() as conn:
        for table in ALL_TABLES:
            sqlite_count = sqlite_fetch_all(f"SELECT COUNT(*) as c FROM {table}")[0]["c"]
            entry = {"sqlite_count": sqlite_count, "supabase_exists": False, "supabase_count": None}
            try:
                row = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
                entry["supabase_exists"] = True
                entry["supabase_count"] = row["c"]
                if row["c"] > 0:
                    report["warnings"].append(
                        f"{table}: Supabase already has {row['c']} row(s) -- confirm this is "
                        f"expected (e.g. a prior partial migration) before proceeding, since this "
                        f"target should normally be empty/staging."
                    )
            except Exception as e:
                report["warnings"].append(f"{table}: table does not exist yet in Supabase or is "
                                           f"not queryable ({e}). Run database/migrations/*.sql first.")
            report["tables"][table] = entry

    schema_missing = [t for t, e in report["tables"].items() if not e["supabase_exists"]]
    report["ready_for_dry_run"] = report["reachable"] and not schema_missing
    if schema_missing:
        report["warnings"].insert(0, f"Missing tables in Supabase: {schema_missing} -- "
                                      f"apply database/migrations/001-010 via the Supabase SQL Editor first.")

    return report
