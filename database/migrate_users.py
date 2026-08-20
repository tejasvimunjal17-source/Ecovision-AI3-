"""
database/migrate_users.py
----------------------------
STEP 5D-2 -- users-table migration utility (SQLite -> Supabase
PostgreSQL). NOT imported by app.py, database/db.py,
database/db_router.py, backend/auth.py, or any page. Nothing in the
running application calls this module or is affected by its existence.
This is a standalone tool a human operator runs explicitly (e.g. from a
shell: `python3 -c "from database.migrate_users import migrate_users;
print(migrate_users(dry_run=True))"`), reviewed and re-run as needed,
independent of USE_SUPABASE (which stays false regardless).

WHAT THIS DOES: copies rows from SQLite's `users` table into a
Supabase Postgres `users` table (schema per
database/migrations/001_init_schema.sql), preserving every value
exactly -- same id, same password_hash/salt (no rehashing), same role,
same timestamps. It does not touch complaints, rewards, chat history,
notifications, carbon records, recycling centres, or any other table,
and does not create or modify Supabase Auth users -- see "SUPABASE
AUTH -- DEFERRED" below.

WHY IDS MUST BE PRESERVED: 9 foreign keys elsewhere in the schema
reference users.id (complaints.user_id, complaints.assigned_officer_id,
complaint_timeline.changed_by, rewards.user_id, chat_history.user_id,
carbon_records.user_id, login_attempts has none but audit_log.user_id
does, notifications.created_by, notification_recipients.user_id).
Assigning new, different ids during migration would silently break
every one of those relationships for a later data migration step. This
tool uses Postgres's `OVERRIDING SYSTEM VALUE` (valid for a `GENERATED
ALWAYS AS IDENTITY` column, which is exactly how migration 001 defines
users.id) to insert each row with its original SQLite id explicitly,
then repairs the identity sequence afterward so future ordinary inserts
don't collide with a migrated id.

CONFLICT STRATEGY -- never overwrites an existing Supabase row. Every
SQLite user is checked against a snapshot of what's already in Supabase
before insert; on any of the conflict types below, that row is SKIPPED
(not migrated, not modified, not deleted) and reported with a reason:
    1. SQLite user's id already exists in Supabase          -> skip
    2. SQLite user's email already exists in Supabase        -> skip
       (regardless of which id it's under)
    3. (same as 2 -- "email exists under a different id" is exactly
       the case above; called out separately here because it's the
       specific scenario the id-preservation strategy is designed to
       avoid silently corrupting)
    4. SQLite user's google_id already exists in Supabase under
       a different user                                      -> skip
    5. auth_user_id conflicts: cannot occur from this tool, because
       SQLite has no auth_user_id column at all (see Step 5B's design
       decision -- the Supabase Auth bridge was deliberately deferred)
       and this tool always inserts NULL for that column. If a future,
       separate process populates auth_user_id independently, this
       tool never writes to it and so can never collide with that.
    6. Any other database error on a specific row (constraint
       violation, type error, etc.) -> that row is rolled back via a
       SAVEPOINT (not the whole migration) and reported as an error;
       every other row is still attempted.

The whole run either commits (successfully migrated + explicitly
skipped rows) or, if something fails before any row-level work even
starts (e.g. the Supabase connection itself), nothing is written at
all -- see database/db_supabase.py's get_connection(), which rolls back
the entire transaction on an uncaught exception.

SUPABASE AUTH -- DEFERRED: per Step 5B's validated design, this tool
does not create Supabase Auth users (no `auth.admin.create_user()`
calls). Every migrated row's auth_user_id is left NULL. Rationale: (a)
Step 5D-2's own instructions require this to be "explicitly proven
necessary and safely testable" before doing it, and it isn't yet
(that's still a placeholder future step -- see Step 5B's audit); (b)
authentication itself does not depend on this table migration at all
-- Email/Google login both continue to work purely through
backend/auth.py against SQLite, completely unaffected by anything in
this file, since USE_SUPABASE stays false and nothing here is wired
into the login path.

ADMIN ACCOUNT NOTE: this tool has no special-case logic for admin rows
-- an admin user migrates exactly like any other row, using the exact
value already stored in password_hash/salt (no hardcoded credential is
embedded in this file or in any migration SQL). One practical warning
worth acting on before running this against a real, shared Supabase
project: database/db.py::_seed_admin() creates a default dev admin
(admin@ecovision.local) with a well-known password documented in this
same codebase's logs and README. If that account still has its
original seeded password, change it (via the existing "forgot
password" flow or a direct password update) BEFORE migrating, since
this tool will faithfully carry over whatever hash is currently
stored -- it has no way to know that hash is a known development
default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from database.db import fetch_all as sqlite_fetch_all

logger = logging.getLogger("ecovision.migrate_users")

USERS_COLUMNS = (
    "id", "full_name", "email", "phone", "password_hash", "salt", "role",
    "auth_provider", "google_id", "ward", "address", "avatar_path",
    "security_question", "security_answer_hash", "reward_points", "is_active",
    "created_at", "last_login",
)


@dataclass
class UserMigrationResult:
    sqlite_id: int
    email: str
    action: str  # "insert" | "would_insert" | "skip" | "would_skip" | "inserted" | "error"
    reason: str = ""


def _plan_migration(sqlite_users: list[dict], existing_supabase_users: list[dict]) -> list[UserMigrationResult]:
    """
    Pure function -- no I/O, no database connection. Given a list of
    SQLite user rows and a snapshot of what already exists in Supabase
    (each a dict with at least id/email/google_id), returns the planned
    action for every SQLite row per the conflict strategy documented
    above. Fully unit-testable without any live database -- see
    tests/test_users_migration.py.
    """
    by_id = {u["id"]: u for u in existing_supabase_users}
    by_email = {u["email"].strip().lower(): u for u in existing_supabase_users}
    by_google_id = {u["google_id"]: u for u in existing_supabase_users if u.get("google_id")}

    plan = []
    for u in sqlite_users:
        email_l = u["email"].strip().lower()

        if u["id"] in by_id:
            existing = by_id[u["id"]]
            if existing["email"].strip().lower() == email_l:
                plan.append(UserMigrationResult(u["id"], u["email"], "skip",
                    f"id {u['id']} already present in Supabase (same email) -- already migrated"))
            else:
                plan.append(UserMigrationResult(u["id"], u["email"], "skip",
                    f"id {u['id']} already exists in Supabase under a different email "
                    f"({existing['email']!r}) -- refusing to overwrite"))
            continue

        if email_l in by_email:
            existing = by_email[email_l]
            plan.append(UserMigrationResult(u["id"], u["email"], "skip",
                f"email already exists in Supabase under id {existing['id']} "
                f"(SQLite id is {u['id']}) -- refusing to overwrite or duplicate"))
            continue

        google_id = u.get("google_id")
        if google_id and google_id in by_google_id:
            existing = by_google_id[google_id]
            plan.append(UserMigrationResult(u["id"], u["email"], "skip",
                f"google_id already exists in Supabase under a different user "
                f"(id {existing['id']}, email {existing['email']!r})"))
            continue

        plan.append(UserMigrationResult(u["id"], u["email"], "insert", "no conflict"))

    return plan


def compare_id_sets(sqlite_users: list[dict], supabase_users: list[dict]) -> dict:
    """
    Pure function -- for post-migration verification (Test C: ID
    preservation). Returns which ids are only in SQLite, only in
    Supabase, or present in both with matching id values.
    """
    sqlite_ids = {u["id"] for u in sqlite_users}
    supabase_ids = {u["id"] for u in supabase_users}
    return {
        "only_in_sqlite": sorted(sqlite_ids - supabase_ids),
        "only_in_supabase": sorted(supabase_ids - sqlite_ids),
        "matched": sorted(sqlite_ids & supabase_ids),
    }


def _row_to_insert_params(u: dict) -> tuple:
    """Builds the parameter tuple for one row's INSERT, in USERS_COLUMNS
    order, with the one necessary type conversion (SQLite's is_active
    0/1 integer -> a real Python bool for Postgres's boolean column).
    password_hash and salt are passed through completely unchanged --
    no rehashing, no re-encoding."""
    return (
        u["id"], u["full_name"], u["email"], u["phone"], u["password_hash"], u["salt"],
        u["role"], u["auth_provider"], u["google_id"], u["ward"], u["address"],
        u["avatar_path"], u["security_question"], u["security_answer_hash"],
        u["reward_points"], bool(u["is_active"]), u["created_at"], u["last_login"],
    )


_INSERT_SQL = (
    "INSERT INTO users (" + ", ".join(USERS_COLUMNS) + ", auth_user_id) "
    "OVERRIDING SYSTEM VALUE VALUES (" + ", ".join(["%s"] * len(USERS_COLUMNS)) + ", NULL)"
)


def migrate_users(dry_run: bool = True) -> list[UserMigrationResult]:
    """
    dry_run=True (default -- the safe choice): computes and returns the
    full plan (what would be inserted vs. skipped and why) without
    opening a write transaction or changing anything in Supabase.

    dry_run=False: actually performs the migration. Each row is
    attempted inside its own SAVEPOINT so one row's failure never
    aborts the rest of the batch (Test G: rollback safety) -- see the
    module docstring's conflict-strategy item 6. After all rows are
    attempted, repairs the `users.id` identity sequence so future
    ordinary Supabase-side inserts don't collide with a migrated id.

    Raises database.db_supabase.SupabaseAdapterError (not a silent
    failure, not a fallback to SQLite) if Supabase isn't configured or
    unreachable -- consistent with every other Step 5 module.
    """
    from database.db_supabase import get_connection  # imported lazily so this
    # module has zero import-time dependency on psycopg being installed --
    # only migrate_users() itself needs it, and only when actually called.

    sqlite_users = sqlite_fetch_all("SELECT * FROM users")

    with get_connection() as conn:
        existing_rows = conn.execute("SELECT id, email, google_id FROM users").fetchall()
        existing = [dict(r) for r in existing_rows]

        plan = _plan_migration(sqlite_users, existing)

        if dry_run:
            for p in plan:
                if p.action == "insert":
                    p.action = "would_insert"
                elif p.action == "skip":
                    p.action = "would_skip"
            return plan

        by_id = {u["id"]: u for u in sqlite_users}
        any_inserted = False
        for p in plan:
            if p.action != "insert":
                continue
            u = by_id[p.sqlite_id]
            try:
                conn.execute("SAVEPOINT row_migration")
                conn.execute(_INSERT_SQL, _row_to_insert_params(u))
                conn.execute("RELEASE SAVEPOINT row_migration")
                p.action = "inserted"
                any_inserted = True
            except Exception as e:
                conn.execute("ROLLBACK TO SAVEPOINT row_migration")
                p.action = "error"
                p.reason = str(e)
                logger.warning("Row migration failed for user id=%s email=%s: %s",
                                u["id"], u["email"], e)

        if any_inserted:
            conn.execute(
                "SELECT setval(pg_get_serial_sequence('users','id'), "
                "COALESCE((SELECT MAX(id) FROM users), 1), true)"
            )

        return plan
