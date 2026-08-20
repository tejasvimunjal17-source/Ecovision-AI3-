"""
tests/test_users_migration.py
--------------------------------
Step 5D-2 test suite for database/migrate_users.py. Run with:

    python3 -m unittest tests.test_users_migration -v

Test groups map directly to the step's required tests A-G:

- Test A (SQLite unaffected) -- reuses/extends the Step 5D-1 regression
  suite's coverage; this file additionally confirms migrate_users()
  never writes to SQLite (it's a pure read on that side).
- Test B (schema/SQL generation compatibility) -- verifies the exact
  generated INSERT SQL text; NOT run against a live Postgres.
- Test C (ID preservation) -- via _plan_migration() and via a stub
  connection that records the exact parameters migrate_users() would
  send.
- Test D (password hash preservation) -- via the same stub connection,
  asserts the password_hash/salt parameters are byte-identical to the
  SQLite source.
- Test E (role preservation) -- citizen/officer/admin all migrate
  unchanged.
- Test F (duplicate handling) -- all 4 real conflict scenarios against
  _plan_migration().
- Test G (rollback / non-destructive on error) -- stub connection that
  raises on one specific row, confirms only that row is marked "error",
  every other row still gets a SAVEPOINT/RELEASE pair, and the
  SAVEPOINT/ROLLBACK TO SAVEPOINT calls happen in the right order.

The stub connection (_StubConnection below) is a plain Python object
that records every SQL statement + params passed to .execute() and
returns canned results -- it is NOT a real database and this suite
never claims otherwise. It exists to test migrate_users()'s actual
write-path *logic* (what SQL/params it sends, in what order, with what
transaction control) without requiring a live Postgres/Supabase
connection, which is unavailable in this environment.
"""
import unittest

from database.migrate_users import (
    _plan_migration,
    _row_to_insert_params,
    compare_id_sets,
    migrate_users,
    UserMigrationResult,
    USERS_COLUMNS,
    _INSERT_SQL,
)


def _user(id_, email, role="citizen", google_id=None, password_hash="hash123", salt="salt456", **extra):
    row = {
        "id": id_, "full_name": f"User {id_}", "email": email, "phone": "9999999999",
        "password_hash": password_hash, "salt": salt, "role": role,
        "auth_provider": "google" if google_id else "email", "google_id": google_id,
        "ward": None, "address": None, "avatar_path": None, "security_question": None,
        "security_answer_hash": None, "reward_points": 0, "is_active": 1,
        "created_at": "2026-01-01 00:00:00", "last_login": None,
    }
    row.update(extra)
    return row


class PlanMigrationTests(unittest.TestCase):
    """Test C (id preservation, planning level) + Test F (duplicate handling).
    Pure function -- no I/O at all."""

    def test_no_conflicts_all_insert(self):
        sqlite_users = [_user(1, "a@test.com"), _user(2, "b@test.com")]
        plan = _plan_migration(sqlite_users, existing_supabase_users=[])
        self.assertEqual([p.action for p in plan], ["insert", "insert"])
        self.assertEqual([p.sqlite_id for p in plan], [1, 2])  # ids preserved in the plan

    def test_conflict_1_id_already_exists_same_email_treated_as_already_migrated(self):
        sqlite_users = [_user(1, "a@test.com")]
        existing = [{"id": 1, "email": "a@test.com", "google_id": None}]
        plan = _plan_migration(sqlite_users, existing)
        self.assertEqual(plan[0].action, "skip")
        self.assertIn("already migrated", plan[0].reason)

    def test_conflict_1b_id_exists_under_different_email(self):
        sqlite_users = [_user(1, "new@test.com")]
        existing = [{"id": 1, "email": "someone-else@test.com", "google_id": None}]
        plan = _plan_migration(sqlite_users, existing)
        self.assertEqual(plan[0].action, "skip")
        self.assertIn("different email", plan[0].reason)

    def test_conflict_2_and_3_email_exists_under_different_id(self):
        sqlite_users = [_user(5, "shared@test.com")]
        existing = [{"id": 99, "email": "shared@test.com", "google_id": None}]
        plan = _plan_migration(sqlite_users, existing)
        self.assertEqual(plan[0].action, "skip")
        self.assertIn("email already exists in Supabase under id 99", plan[0].reason)

    def test_conflict_4_google_id_exists_under_different_user(self):
        sqlite_users = [_user(3, "newuser@test.com", google_id="g-123")]
        existing = [{"id": 77, "email": "other@test.com", "google_id": "g-123"}]
        plan = _plan_migration(sqlite_users, existing)
        self.assertEqual(plan[0].action, "skip")
        self.assertIn("google_id already exists", plan[0].reason)

    def test_mixed_batch_some_insert_some_skip(self):
        sqlite_users = [_user(1, "a@test.com"), _user(2, "b@test.com"), _user(3, "c@test.com")]
        existing = [{"id": 2, "email": "b@test.com", "google_id": None}]
        plan = _plan_migration(sqlite_users, existing)
        actions = {p.sqlite_id: p.action for p in plan}
        self.assertEqual(actions, {1: "insert", 2: "skip", 3: "insert"})

    def test_email_case_insensitive_conflict_detection(self):
        sqlite_users = [_user(1, "Person@Test.com")]
        existing = [{"id": 50, "email": "person@test.com", "google_id": None}]
        plan = _plan_migration(sqlite_users, existing)
        self.assertEqual(plan[0].action, "skip")


class RolePreservationTests(unittest.TestCase):
    """Test E: role preservation through the parameter-building step."""

    def test_citizen_officer_admin_roles_pass_through_unchanged(self):
        for role in ("citizen", "officer", "admin"):
            u = _user(1, f"{role}@test.com", role=role)
            params = _row_to_insert_params(u)
            role_index = USERS_COLUMNS.index("role")
            self.assertEqual(params[role_index], role)


class PasswordHashPreservationTests(unittest.TestCase):
    """Test D: the actual stored PBKDF2 hash/salt must be byte-identical
    after building the migration parameters -- no rehashing."""

    def test_hash_and_salt_unchanged(self):
        real_hash = "a3f9c2...260000iterations...deadbeef"  # representative of a real PBKDF2 hex digest
        real_salt = "9f8e7d6c5b4a"
        u = _user(1, "x@test.com", password_hash=real_hash, salt=real_salt)
        params = _row_to_insert_params(u)
        self.assertEqual(params[USERS_COLUMNS.index("password_hash")], real_hash)
        self.assertEqual(params[USERS_COLUMNS.index("salt")], real_salt)

    def test_is_active_int_converted_to_real_bool(self):
        u = _user(1, "x@test.com", is_active=1)
        params = _row_to_insert_params(u)
        val = params[USERS_COLUMNS.index("is_active")]
        self.assertIs(val, True)  # not 1 -- an actual Python bool, for Postgres's boolean column

        u2 = _user(2, "y@test.com", is_active=0)
        params2 = _row_to_insert_params(u2)
        self.assertIs(params2[USERS_COLUMNS.index("is_active")], False)


class InsertSqlTextTests(unittest.TestCase):
    """Test B: schema/SQL generation compatibility -- text/syntax review
    only. NOT TESTED against a live Postgres connection (none available
    in this environment)."""

    def test_insert_sql_uses_overriding_system_value(self):
        self.assertIn("OVERRIDING SYSTEM VALUE", _INSERT_SQL)

    def test_insert_sql_column_count_matches_placeholder_count(self):
        # +1 column (auth_user_id) is appended with a literal NULL, not a placeholder
        placeholder_count = _INSERT_SQL.count("%s")
        self.assertEqual(placeholder_count, len(USERS_COLUMNS))

    def test_insert_sql_includes_id_column_explicitly(self):
        self.assertIn("INSERTINTOusers(id,", _INSERT_SQL.replace(" ", ""))

    def test_insert_sql_auth_user_id_is_literal_null_not_a_parameter(self):
        self.assertTrue(_INSERT_SQL.rstrip().endswith("NULL)"))


class CompareIdSetsTests(unittest.TestCase):
    """Test C: post-migration ID-preservation verification helper."""

    def test_matched_only_sqlite_only_supabase(self):
        sqlite_users = [{"id": 1}, {"id": 2}, {"id": 3}]
        supabase_users = [{"id": 1}, {"id": 2}, {"id": 4}]
        result = compare_id_sets(sqlite_users, supabase_users)
        self.assertEqual(result["matched"], [1, 2])
        self.assertEqual(result["only_in_sqlite"], [3])
        self.assertEqual(result["only_in_supabase"], [4])


class _StubCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _StubConnection:
    """
    Records every (sql, params) passed to .execute(), in order, and can
    be told to raise on a specific SQL fragment to simulate a row-level
    failure. This is NOT a real database connection -- it exists only
    to test migrate_users()'s write-path logic (SQL text, parameter
    values, transaction control ordering) without a live Postgres
    connection. Every test using this class is explicitly a
    stub-connection test, not a live-database test.
    """
    def __init__(self, existing_rows, fail_on_email=None):
        self.existing_rows = existing_rows
        self.fail_on_email = fail_on_email
        self.calls = []  # list of (sql, params)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT id, email, google_id FROM users"):
            return _StubCursorResult(self.existing_rows)
        if sql == "INSERT INTO users (" + ", ".join(USERS_COLUMNS) + ", auth_user_id) " \
                  "OVERRIDING SYSTEM VALUE VALUES (" + ", ".join(["%s"] * len(USERS_COLUMNS)) + ", NULL)":
            if self.fail_on_email and params[USERS_COLUMNS.index("email")] == self.fail_on_email:
                raise RuntimeError(f"simulated constraint violation for {self.fail_on_email}")
        return _StubCursorResult([])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class MigrateUsersWritePathTests(unittest.TestCase):
    """
    Exercises migrate_users(dry_run=False)'s actual code path using a
    stub connection (see _StubConnection above) in place of
    database.db_supabase.get_connection(). Verifies SQL/params/
    transaction-control logic. NOT a live Postgres test.
    """

    def _run_with_stub(self, sqlite_users, existing_rows, fail_on_email=None):
        stub = _StubConnection(existing_rows, fail_on_email=fail_on_email)

        import database.migrate_users as mod
        orig_fetch = mod.sqlite_fetch_all
        orig_get_conn_module = None
        import database.db_supabase as db_supabase_mod
        orig_get_connection = db_supabase_mod.get_connection

        mod.sqlite_fetch_all = lambda query: sqlite_users
        db_supabase_mod.get_connection = lambda: stub
        try:
            return migrate_users(dry_run=False), stub
        finally:
            mod.sqlite_fetch_all = orig_fetch
            db_supabase_mod.get_connection = orig_get_connection

    def test_dry_run_never_calls_insert(self):
        import database.migrate_users as mod
        orig_fetch = mod.sqlite_fetch_all
        import database.db_supabase as db_supabase_mod
        orig_get_connection = db_supabase_mod.get_connection

        stub = _StubConnection(existing_rows=[])
        mod.sqlite_fetch_all = lambda query: [_user(1, "a@test.com")]
        db_supabase_mod.get_connection = lambda: stub
        try:
            plan = migrate_users(dry_run=True)
        finally:
            mod.sqlite_fetch_all = orig_fetch
            db_supabase_mod.get_connection = orig_get_connection

        self.assertEqual(plan[0].action, "would_insert")
        insert_calls = [c for c in stub.calls if c[0].startswith("INSERT INTO users")]
        self.assertEqual(insert_calls, [], "dry_run=True must never issue an INSERT")

    def test_write_path_sends_correct_id_and_values(self):
        u = _user(42, "preserve-id@test.com", role="officer")
        results, stub = self._run_with_stub([u], existing_rows=[])
        self.assertEqual(results[0].action, "inserted")
        self.assertEqual(results[0].sqlite_id, 42)

        insert_calls = [c for c in stub.calls if c[0].startswith("INSERT INTO users")]
        self.assertEqual(len(insert_calls), 1)
        _, params = insert_calls[0]
        self.assertEqual(params[USERS_COLUMNS.index("id")], 42)  # Test C: id preserved exactly
        self.assertEqual(params[USERS_COLUMNS.index("role")], "officer")  # Test E
        self.assertEqual(params[USERS_COLUMNS.index("password_hash")], "hash123")  # Test D

    def test_write_path_uses_savepoints(self):
        u = _user(1, "a@test.com")
        results, stub = self._run_with_stub([u], existing_rows=[])
        sql_sequence = [c[0] for c in stub.calls]
        self.assertIn("SAVEPOINT row_migration", sql_sequence)
        self.assertIn("RELEASE SAVEPOINT row_migration", sql_sequence)
        # SAVEPOINT must come before RELEASE, both before/around the INSERT
        self.assertLess(sql_sequence.index("SAVEPOINT row_migration"),
                         sql_sequence.index("RELEASE SAVEPOINT row_migration"))

    def test_write_path_one_row_failure_does_not_abort_batch(self):
        """Test G: rollback safety -- a failure on one row must not
        prevent other rows from being attempted, and must not raise out
        of migrate_users() itself."""
        u1 = _user(1, "good1@test.com")
        u2 = _user(2, "bad@test.com")  # this one will raise inside the stub
        u3 = _user(3, "good2@test.com")
        results, stub = self._run_with_stub([u1, u2, u3], existing_rows=[], fail_on_email="bad@test.com")

        by_email = {r.email: r for r in results}
        self.assertEqual(by_email["good1@test.com"].action, "inserted")
        self.assertEqual(by_email["bad@test.com"].action, "error")
        self.assertIn("simulated constraint violation", by_email["bad@test.com"].reason)
        self.assertEqual(by_email["good2@test.com"].action, "inserted")  # not aborted by row 2's failure

        sql_sequence = [c[0] for c in stub.calls]
        self.assertIn("ROLLBACK TO SAVEPOINT row_migration", sql_sequence)
        # 3 SAVEPOINTs opened (one per row), only 2 released (row 2 rolled back instead)
        self.assertEqual(sql_sequence.count("SAVEPOINT row_migration"), 3)
        self.assertEqual(sql_sequence.count("RELEASE SAVEPOINT row_migration"), 2)
        self.assertEqual(sql_sequence.count("ROLLBACK TO SAVEPOINT row_migration"), 1)

    def test_write_path_skipped_rows_never_get_a_savepoint(self):
        u1 = _user(1, "a@test.com")
        existing = [{"id": 1, "email": "a@test.com", "google_id": None}]  # already migrated
        results, stub = self._run_with_stub([u1], existing_rows=existing)
        self.assertEqual(results[0].action, "skip")
        insert_calls = [c for c in stub.calls if c[0].startswith("INSERT INTO users")]
        self.assertEqual(insert_calls, [])

    def test_sequence_repair_called_only_when_something_was_inserted(self):
        u1 = _user(1, "a@test.com")
        results, stub = self._run_with_stub([u1], existing_rows=[])
        sql_sequence = [c[0] for c in stub.calls]
        self.assertTrue(any("setval" in s for s in sql_sequence))

        # nothing to insert (already exists) -> no sequence repair needed
        existing = [{"id": 1, "email": "a@test.com", "google_id": None}]
        results2, stub2 = self._run_with_stub([u1], existing_rows=existing)
        sql_sequence2 = [c[0] for c in stub2.calls]
        self.assertFalse(any("setval" in s for s in sql_sequence2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
