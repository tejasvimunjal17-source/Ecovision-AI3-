"""
tests/test_migration_verification.py
----------------------------------------
Step 5D-4 test suite for database/verify_migration.py. Run with:

    python3 -m unittest tests.test_migration_verification -v

Every test that needs "Supabase-side" data uses a stub connection (see
_StubConnection below) -- the same, explicitly-documented technique
already used in tests/test_users_migration.py and
tests/test_data_migration.py. NOT a live Postgres/Supabase connection.
Tests that only need the SQLite side (via database.migrate_data's
already-tested compare_counts/compare_id_sets, which open a connection
through database.db_supabase.get_connection -- monkeypatched to the
stub here too) get real SQLite reads through a temporary database.

No test in this file uses fake Supabase credentials and calls the
result a verified live migration -- see NoLiveConnectionTests, which
explicitly confirms the loud-failure behavior when nothing is
configured, and is the only place "NOT RUN" is asserted rather than a
comparison result.
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone


class _StubCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubConnection:
    """
    A fake "Supabase" connection: a plain in-memory table store keyed by
    table name, queried via a handful of recognized SQL shapes (COUNT(*),
    SELECT id, SELECT *, SELECT <cols> WHERE id IN (...), SELECT DISTINCT
    language). This is NOT a real database -- it exists purely to test
    database/verify_migration.py's comparison logic (what it flags as a
    mismatch, in what report field) without a live Postgres connection.
    """

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables  # {table_name: [row_dict, ...]}
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(sql)
        sql_stripped = sql.strip()

        for table, rows in self.tables.items():
            if sql_stripped.startswith(f"SELECT COUNT(*)") and f"FROM {table}" in sql_stripped:
                return _StubCursorResult([{"c": len(rows)}])
            if sql_stripped == f"SELECT id FROM {table}":
                return _StubCursorResult([{"id": r["id"]} for r in rows])
            if sql_stripped == f"SELECT * FROM {table}":
                return _StubCursorResult(rows)
            if sql_stripped.startswith(f"SELECT DISTINCT language FROM {table}"):
                langs = sorted({r["language"] for r in rows})
                return _StubCursorResult([{"language": l} for l in langs])
            if sql_stripped.startswith("SELECT id, image_path FROM complaints") and table == "complaints":
                return _StubCursorResult([{"id": r["id"], "image_path": r.get("image_path")} for r in rows])
            if sql_stripped.startswith(f"SELECT id, ") and f"FROM {table}" in sql_stripped:
                # e.g. "SELECT id, status FROM complaints"
                col = sql_stripped.split("SELECT id, ")[1].split(" FROM")[0]
                return _StubCursorResult([{"id": r["id"], col: r.get(col)} for r in rows])

        return _StubCursorResult([])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _TempSqliteDbMixin:
    def setUp(self):
        self.tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        os.remove(self.tmpdb)
        from config import settings
        self._orig_path = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.tmpdb

    def tearDown(self):
        from config import settings
        settings.DATABASE_PATH = self._orig_path
        if os.path.exists(self.tmpdb):
            os.remove(self.tmpdb)

    def _patch_supabase(self, stub):
        import database.db_supabase as db_supabase_mod
        orig = db_supabase_mod.get_connection
        db_supabase_mod.get_connection = lambda: stub
        self.addCleanup(lambda: setattr(db_supabase_mod, "get_connection", orig))


class MatchingCountsTests(_TempSqliteDbMixin, unittest.TestCase):
    """Test: matching counts -> PASS."""

    def test_matching_categories_table_passes(self):
        from database.db import init_db, fetch_all
        init_db()
        sqlite_rows = fetch_all("SELECT * FROM categories")
        # Supabase-side rows must reflect what a CORRECT migration
        # produces: is_active as a real bool, not SQLite's raw 0/1 --
        # otherwise this "matching" fixture would itself fail the
        # boolean-type check, which isn't what this test is about.
        supabase_rows = [dict(r, is_active=bool(r["is_active"])) for r in sqlite_rows]
        stub = _StubConnection({"categories": supabase_rows})
        self._patch_supabase(stub)

        from database.verify_migration import verify_table
        report = verify_table("categories")
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.ids["only_in_sqlite"], [])
        self.assertEqual(report.ids["only_in_supabase"], [])


class MissingAndUnexpectedIdTests(_TempSqliteDbMixin, unittest.TestCase):
    """Tests: missing IDs, unexpected IDs -> FAIL with the right reason."""

    def test_missing_id_in_supabase_fails(self):
        from database.db import init_db, fetch_all
        init_db()
        sqlite_rows = [dict(r) for r in fetch_all("SELECT * FROM categories")]
        supabase_rows = sqlite_rows[:-1]  # one category never made it to "Supabase"
        stub = _StubConnection({"categories": supabase_rows})
        self._patch_supabase(stub)

        from database.verify_migration import verify_table
        report = verify_table("categories")
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(len(report.ids["only_in_sqlite"]), 1)
        self.assertIn("missing from Supabase", " ".join(report.notes))

    def test_unexpected_id_in_supabase_fails(self):
        from database.db import init_db, fetch_all
        init_db()
        sqlite_rows = [dict(r) for r in fetch_all("SELECT * FROM categories")]
        extra_row = dict(sqlite_rows[0])
        extra_row["id"] = 999999  # doesn't exist in SQLite
        supabase_rows = sqlite_rows + [extra_row]
        stub = _StubConnection({"categories": supabase_rows})
        self._patch_supabase(stub)

        from database.verify_migration import verify_table
        report = verify_table("categories")
        self.assertEqual(report.status, "FAIL")
        self.assertEqual(report.ids["only_in_supabase"], [999999])
        self.assertIn("unexpected id", " ".join(report.notes))


class ForeignKeyIntegrityTests(_TempSqliteDbMixin, unittest.TestCase):
    """Test: FK failures -> flagged, with the offending ids."""

    def test_fk_violation_detected(self):
        from database.db import init_db, execute, fetch_all
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        cid = execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)", (uid, "Plastic", "Resolved"))
        complaints = [dict(r) for r in fetch_all("SELECT * FROM complaints")]

        # Supabase copy: complaint exists, but its referenced user does NOT
        # (simulates a users-migration gap)
        stub = _StubConnection({"complaints": complaints, "users": []})
        self._patch_supabase(stub)

        from database.verify_migration import verify_foreign_keys
        violations = verify_foreign_keys("complaints")
        self.assertIn("user_id", violations)
        self.assertIn(cid, violations["user_id"])

    def test_no_fk_violation_when_users_present(self):
        from database.db import init_db, execute, fetch_all
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)", (uid, "Plastic", "Resolved"))
        complaints = [dict(r) for r in fetch_all("SELECT * FROM complaints")]
        users = [dict(r) for r in fetch_all("SELECT * FROM users")]

        stub = _StubConnection({"complaints": complaints, "users": users})
        self._patch_supabase(stub)

        from database.verify_migration import verify_foreign_keys
        violations = verify_foreign_keys("complaints")
        self.assertEqual(violations, {})

    def test_notification_recipients_relationship_validated(self):
        from database.db import init_db, execute, fetch_all
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        admin_id = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                           ("A", "a@test.com", "9", "x", "y", "admin"))
        nid = execute("INSERT INTO notifications (title, message, audience, created_by) VALUES (?,?,?,?)",
                      ("Hi", "msg", "all", admin_id))
        execute("INSERT INTO notification_recipients (notification_id, user_id) VALUES (?,?)", (nid, uid))

        recipients = [dict(r) for r in fetch_all("SELECT * FROM notification_recipients")]
        notifications = [dict(r) for r in fetch_all("SELECT * FROM notifications")]
        users = [dict(r) for r in fetch_all("SELECT * FROM users")]

        # missing notification on the Supabase side -> violation on notification_id
        stub = _StubConnection({"notification_recipients": recipients, "notifications": [], "users": users})
        self._patch_supabase(stub)
        from database.verify_migration import verify_foreign_keys
        violations = verify_foreign_keys("notification_recipients")
        self.assertIn("notification_id", violations)

        # now with notifications present too -> clean
        stub2 = _StubConnection({"notification_recipients": recipients, "notifications": notifications, "users": users})
        self._patch_supabase(stub2)
        violations2 = verify_foreign_keys("notification_recipients")
        self.assertEqual(violations2, {})


class StatusValidationTests(_TempSqliteDbMixin, unittest.TestCase):
    """Test: complaints.status validation."""

    def test_invalid_status_detected(self):
        from database.verify_migration import verify_status_values
        stub = _StubConnection({"complaints": [{"id": 1, "status": "TotallyMadeUp"}, {"id": 2, "status": "Resolved"}]})
        self._patch_supabase(stub)
        bad = verify_status_values("complaints")
        self.assertEqual(bad, [1])

    def test_all_valid_statuses_pass(self):
        from database.verify_migration import verify_status_values
        stub = _StubConnection({"complaints": [{"id": 1, "status": "Resolved"}, {"id": 2, "status": "Submitted"}]})
        self._patch_supabase(stub)
        bad = verify_status_values("complaints")
        self.assertEqual(bad, [])


class BooleanConversionTests(unittest.TestCase):
    """Test: boolean conversion -- confirms Supabase actually stores a
    real bool, not 0/1."""

    def test_correct_bool_type_passes(self):
        from database.verify_migration import verify_type_conversions
        stub = _StubConnection({"categories": [{"id": 1, "is_active": True}]})
        import database.db_supabase as m
        orig = m.get_connection
        m.get_connection = lambda: stub
        try:
            issues = verify_type_conversions("categories")
        finally:
            m.get_connection = orig
        self.assertEqual(issues, [])

    def test_integer_instead_of_bool_flagged(self):
        from database.verify_migration import verify_type_conversions
        stub = _StubConnection({"categories": [{"id": 1, "is_active": 1}]})  # wrong type: int, not bool
        import database.db_supabase as m
        orig = m.get_connection
        m.get_connection = lambda: stub
        try:
            issues = verify_type_conversions("categories")
        finally:
            m.get_connection = orig
        self.assertEqual(len(issues), 1)
        self.assertIn("expected bool", issues[0])


class TimestampValidationTests(unittest.TestCase):
    """Test: timestamp validation -- confirms Supabase actually stores a
    real datetime, not a leftover string."""

    def test_real_datetime_passes(self):
        from database.verify_migration import verify_type_conversions
        stub = _StubConnection({"complaints": [{"id": 1, "created_at": datetime.now(timezone.utc),
                                                  "updated_at": None, "resolved_at": None}]})
        import database.db_supabase as m
        orig = m.get_connection
        m.get_connection = lambda: stub
        try:
            issues = verify_type_conversions("complaints")
        finally:
            m.get_connection = orig
        self.assertEqual(issues, [])

    def test_string_timestamp_flagged(self):
        from database.verify_migration import verify_type_conversions
        stub = _StubConnection({"complaints": [{"id": 1, "created_at": "2026-08-18 10:00:00",
                                                  "updated_at": None, "resolved_at": None}]})
        import database.db_supabase as m
        orig = m.get_connection
        m.get_connection = lambda: stub
        try:
            issues = verify_type_conversions("complaints")
        finally:
            m.get_connection = orig
        self.assertEqual(len(issues), 1)
        self.assertIn("expected datetime", issues[0])


class ChatLanguageAndImagePathTests(_TempSqliteDbMixin, unittest.TestCase):
    def test_chat_language_values_reported(self):
        from database.verify_migration import verify_chat_language
        stub = _StubConnection({"chat_history": [{"id": 1, "language": "en"}, {"id": 2, "language": "hi"}]})
        self._patch_supabase(stub)
        self.assertEqual(verify_chat_language(), {"en", "hi"})

    def test_image_path_unchanged_passes(self):
        from database.db import init_db, execute
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        cid = execute("INSERT INTO complaints (user_id, category, image_path) VALUES (?,?,?)",
                      (uid, "Plastic", "/uploads/img1.jpg"))

        stub = _StubConnection({"complaints": [{"id": cid, "image_path": "/uploads/img1.jpg"}]})
        self._patch_supabase(stub)

        from database.verify_migration import verify_image_paths
        result = verify_image_paths()
        self.assertEqual(result["mismatches"], [])

    def test_image_path_changed_flagged(self):
        from database.db import init_db, execute
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        cid = execute("INSERT INTO complaints (user_id, category, image_path) VALUES (?,?,?)",
                      (uid, "Plastic", "/uploads/original.jpg"))

        stub = _StubConnection({"complaints": [{"id": cid, "image_path": "/uploads/DIFFERENT.jpg"}]})
        self._patch_supabase(stub)

        from database.verify_migration import verify_image_paths
        result = verify_image_paths()
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertEqual(result["mismatches"][0]["sqlite"], "/uploads/original.jpg")


class DryRunAndNoWriteTests(_TempSqliteDbMixin, unittest.TestCase):
    """Tests: dry-run behavior, no-write guarantee."""

    def test_dry_run_does_not_run_per_table_comparison(self):
        stub = _StubConnection({})
        self._patch_supabase(stub)

        from database.verify_migration import verify_all
        result = verify_all(dry_run=True)
        self.assertEqual(result["tables"], {})
        self.assertIn("NOT RUN", result["overall"])

    def test_dry_run_still_only_issues_select_statements(self):
        """No-write guarantee: every SQL statement the stub recorded
        during a dry run must be a SELECT."""
        stub = _StubConnection({})
        self._patch_supabase(stub)
        from database.verify_migration import verify_all
        verify_all(dry_run=True)
        for sql in stub.calls:
            self.assertTrue(sql.strip().upper().startswith("SELECT"), f"non-SELECT call made: {sql}")

    def test_full_run_issues_only_select_statements(self):
        from database.db import init_db, fetch_all
        init_db()
        tables = {}
        for t in ("categories", "recycling_centres", "login_attempts", "complaints", "rewards",
                  "chat_history", "carbon_records", "audit_log", "notifications",
                  "complaint_timeline", "notification_recipients", "users"):
            tables[t] = [dict(r) for r in fetch_all(f"SELECT * FROM {t}")]
        stub = _StubConnection(tables)
        self._patch_supabase(stub)

        from database.verify_migration import verify_all
        result = verify_all(dry_run=False)
        self.assertIn(result["overall"], ("PASS", "FAIL"))
        for sql in stub.calls:
            self.assertTrue(sql.strip().upper().startswith("SELECT"), f"non-SELECT call made: {sql}")


class NoLiveConnectionTests(unittest.TestCase):
    """
    Explicit confirmation of the "safe failure when Supabase is not
    configured" requirement, and that this suite never claims a live
    Supabase verification occurred. This is the ONLY place in this file
    that touches the real (unpatched) database.db_supabase module.
    """

    def setUp(self):
        from config import settings
        self._orig = settings.SUPABASE_DB_URL
        settings.SUPABASE_DB_URL = ""

    def tearDown(self):
        from config import settings
        settings.SUPABASE_DB_URL = self._orig

    def test_verify_all_raises_when_unconfigured(self):
        from database.verify_migration import verify_all
        from database.db_supabase import SupabaseAdapterError
        with self.assertRaises(SupabaseAdapterError):
            verify_all(dry_run=True)
        with self.assertRaises(SupabaseAdapterError):
            verify_all(dry_run=False)

    def test_live_supabase_verification_is_explicitly_not_run_in_this_suite(self):
        """Documents, in an actual assertion, that this test suite never
        performs a real Supabase verification -- consistent with the
        report's 'NOT RUN' labeling."""
        from config import settings
        self.assertFalse(settings.is_supabase_db_configured())


if __name__ == "__main__":
    unittest.main(verbosity=2)
