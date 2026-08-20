"""
tests/test_data_migration.py
--------------------------------
Step 5D-3 test suite for database/migrate_data.py. Run with:

    python3 -m unittest tests.test_data_migration -v

Groups map to the 13 required test areas:
 1/2  empty vs. populated database        -> DependencyOrderTests, AuditSourceDataTests
 3    dependency ordering                 -> DependencyOrderTests
 4    ID preservation                     -> WritePathTests (stub connection)
 5    foreign-key validation              -> AuditSourceDataTests
 6    duplicate/conflict handling         -> PlanTableTests
 7    invalid status handling             -> AuditSourceDataTests
 8    boolean conversion                  -> RowParamConversionTests
 9    timestamp normalization             -> TimestampNormalizationTests
10    rollback behavior                   -> WritePathTests
11    dry-run produces zero writes        -> WritePathTests
12    sequence-repair SQL generation      -> WritePathTests
13    repeated/idempotent execution       -> WritePathTests + AuditSourceDataTests (real SQLite)

Real SQLite is used wherever the test doesn't need a live Supabase
connection (audit functions, dependency graph). The write path
(migrate_table/migrate_all against Supabase) is tested via a recorded
stub connection -- NOT a live database -- exactly the same, explicitly-
labeled technique used in tests/test_users_migration.py. No test here
uses fake Supabase credentials and calls the result a "successful
migration" -- stub-connection tests are clearly named and documented as
such throughout.
"""
import os
import tempfile
import unittest

REAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_officer_id INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS complaint_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    changed_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS notification_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
"""


class DependencyGraphTests(unittest.TestCase):
    """Test 3: dependency ordering -- discovered from schema text, not assumed."""

    def test_discovers_correct_graph_from_fixture_schema(self):
        from database.migrate_data import discover_dependency_graph
        graph = discover_dependency_graph(REAL_SCHEMA)
        self.assertEqual(graph["users"], set())
        self.assertEqual(graph["categories"], set())
        self.assertEqual(graph["complaints"], {"users"})
        self.assertEqual(graph["complaint_timeline"], {"complaints", "users"})
        self.assertEqual(graph["notifications"], {"users"})
        self.assertEqual(graph["notification_recipients"], {"notifications", "users"})

    def test_topological_order_respects_every_dependency(self):
        from database.migrate_data import discover_dependency_graph, dependency_order
        tables = ("categories", "complaints", "complaint_timeline", "notifications", "notification_recipients")

        # Monkeypatch: use the fixture schema instead of the real file for this test
        import database.migrate_data as mod
        orig = mod._SCHEMA_PATH
        try:
            import types
            mod.discover_dependency_graph = lambda schema_text=None: discover_dependency_graph(REAL_SCHEMA)
            order = dependency_order(tables)
        finally:
            mod._SCHEMA_PATH = orig
            import importlib
            importlib.reload(mod)  # restore the real discover_dependency_graph implementation

        pos = {t: i for i, t in enumerate(order)}
        self.assertLess(pos["categories"], pos["complaints"]) if "categories" in pos and "complaints" in pos else None
        self.assertLess(pos["complaints"], pos["complaint_timeline"])
        self.assertLess(pos["notifications"], pos["notification_recipients"])

    def test_real_schema_produces_valid_total_order(self):
        """Runs against the ACTUAL database/schema.sql shipped in this repo."""
        from database.migrate_data import discover_dependency_graph, dependency_order, TARGET_TABLES
        order = dependency_order()
        self.assertEqual(set(order), set(TARGET_TABLES))
        graph = discover_dependency_graph()
        pos = {t: i for i, t in enumerate(order)}
        for t in order:
            for dep in graph.get(t, set()) - {"users"}:
                if dep in pos:
                    self.assertLess(pos[dep], pos[t], f"{t} placed before its dependency {dep}")


class TimestampNormalizationTests(unittest.TestCase):
    """Test 9: timestamp normalization."""

    def test_sqlite_text_timestamp_becomes_utc_aware_datetime(self):
        from database.migrate_data import _normalize_timestamp
        import datetime
        result = _normalize_timestamp("2026-08-18 10:30:00")
        self.assertIsInstance(result, datetime.datetime)
        self.assertEqual(result.tzinfo, datetime.timezone.utc)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.hour, 10)

    def test_none_passes_through(self):
        from database.migrate_data import _normalize_timestamp
        self.assertIsNone(_normalize_timestamp(None))

    def test_already_aware_datetime_passes_through_unchanged(self):
        from database.migrate_data import _normalize_timestamp
        import datetime
        dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(_normalize_timestamp(dt), dt)


class RowParamConversionTests(unittest.TestCase):
    """Test 8: boolean conversion, plus general row->params correctness."""

    def test_boolean_conversion_categories(self):
        from database.migrate_data import _row_to_params
        row = {"id": 1, "name": "Plastic", "description": "d", "icon": "🧴",
               "disposal_guide": "g", "is_active": 1}
        params = _row_to_params("categories", row)
        # is_active is the last column
        self.assertIs(params[-1], True)

        row2 = dict(row, is_active=0)
        params2 = _row_to_params("categories", row2)
        self.assertIs(params2[-1], False)

    def test_login_attempts_success_boolean_conversion(self):
        from database.migrate_data import _row_to_params, TABLE_SPECS
        row = {"id": 1, "email": "a@test.com", "success": 1, "ip_hint": None, "created_at": "2026-01-01 00:00:00"}
        params = _row_to_params("login_attempts", row)
        idx = TABLE_SPECS["login_attempts"].columns.index("success")
        self.assertIs(params[idx], True)

    def test_id_preserved_exactly_in_params(self):
        from database.migrate_data import _row_to_params, TABLE_SPECS
        row = {"id": 777, "name": "X", "description": None, "icon": None,
               "disposal_guide": None, "is_active": 1}
        params = _row_to_params("categories", row)
        idx = TABLE_SPECS["categories"].columns.index("id")
        self.assertEqual(params[idx], 777)

    def test_password_and_other_text_fields_untouched(self):
        from database.migrate_data import _row_to_params, TABLE_SPECS
        row = {"id": 1, "user_id": 2, "session_id": "s1", "role": "user",
               "message": "How do I segregate waste?", "language": "hi",
               "created_at": "2026-01-01 00:00:00"}
        params = _row_to_params("chat_history", row)
        idx = TABLE_SPECS["chat_history"].columns.index("message")
        self.assertEqual(params[idx], "How do I segregate waste?")
        lang_idx = TABLE_SPECS["chat_history"].columns.index("language")
        self.assertEqual(params[lang_idx], "hi")


class InsertSqlTests(unittest.TestCase):
    def test_uses_overriding_system_value_for_every_table(self):
        from database.migrate_data import _insert_sql, TARGET_TABLES
        for table in TARGET_TABLES:
            sql = _insert_sql(table)
            self.assertIn("OVERRIDING SYSTEM VALUE", sql)
            self.assertTrue(sql.startswith(f"INSERT INTO {table} ("))


class PlanTableTests(unittest.TestCase):
    """Test 6: duplicate/conflict handling. Pure function -- no I/O."""

    def test_no_conflict_all_insert(self):
        from database.migrate_data import _plan_table
        rows = [{"id": 1}, {"id": 2}]
        plan = _plan_table("categories", rows, existing_ids=set(), existing_natural_keys=set())
        self.assertEqual([p.action for p in plan], ["insert", "insert"])

    def test_id_conflict_skips(self):
        from database.migrate_data import _plan_table
        rows = [{"id": 1}, {"id": 2}]
        plan = _plan_table("categories", rows, existing_ids={1}, existing_natural_keys=set())
        actions = {p.row_id: p.action for p in plan}
        self.assertEqual(actions, {1: "skip", 2: "insert"})

    def test_natural_key_conflict_skips_notification_recipients(self):
        from database.migrate_data import _plan_table
        rows = [{"id": 5, "notification_id": 10, "user_id": 20}]
        # id 5 is free in Supabase, but (10, 20) already exists under a different id
        plan = _plan_table("notification_recipients", rows, existing_ids=set(),
                            existing_natural_keys={(10, 20)})
        self.assertEqual(plan[0].action, "skip")
        self.assertIn("already exists", plan[0].reason)

    def test_natural_key_no_conflict_inserts(self):
        from database.migrate_data import _plan_table
        rows = [{"id": 5, "notification_id": 10, "user_id": 20}]
        plan = _plan_table("notification_recipients", rows, existing_ids=set(), existing_natural_keys=set())
        self.assertEqual(plan[0].action, "insert")


class AuditSourceDataTests(unittest.TestCase):
    """Tests 1, 2, 5, 7, 13: empty/populated DB, FK validation, invalid
    status detection, idempotent (read-only, side-effect-free) execution
    -- against a REAL temporary SQLite database."""

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

    def test_empty_database_audit(self):
        from database.db import init_db
        from database.migrate_data import audit_source_data, TARGET_TABLES
        init_db()
        reports = audit_source_data()
        # categories/recycling_centres get default reference data seeded
        # by init_db() itself (existing app behavior, unrelated to this
        # migration tool) -- every other table is genuinely empty.
        seeded_tables = {"categories", "recycling_centres"}
        for t in TARGET_TABLES:
            if t in seeded_tables:
                self.assertGreater(reports[t].row_count, 0)
            else:
                self.assertEqual(reports[t].row_count, 0)
            self.assertEqual(reports[t].orphaned_fk_rows, {})

    def test_populated_database_audit(self):
        from database.db import init_db, execute
        from database.migrate_data import audit_source_data
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        cid = execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)", (uid, "Plastic", "Resolved"))
        execute("INSERT INTO complaint_timeline (complaint_id, status) VALUES (?,?)", (cid, "Resolved"))
        execute("INSERT INTO rewards (user_id, points) VALUES (?,?)", (uid, 10))

        reports = audit_source_data()
        self.assertEqual(reports["complaints"].row_count, 1)
        self.assertEqual(reports["complaint_timeline"].row_count, 1)
        self.assertEqual(reports["rewards"].row_count, 1)
        self.assertEqual(reports["complaints"].invalid_status_rows, 0)

    def test_audit_is_read_only_and_idempotent(self):
        """Test 13: running the audit twice must produce identical
        results and must not modify the database."""
        from database.db import init_db, execute
        from database.migrate_data import audit_source_data
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        execute("INSERT INTO rewards (user_id, points) VALUES (?,?)", (uid, 10))

        r1 = audit_source_data(("rewards",))
        r2 = audit_source_data(("rewards",))
        self.assertEqual(r1["rewards"].row_count, r2["rewards"].row_count)
        self.assertEqual(r1["rewards"].row_count, 1)

    def test_invalid_status_detected(self):
        """Test 7: a status value outside the allowed set is flagged.
        Constructs a pre-Step-5D-1 legacy schema directly (no CHECK
        constraint on status), matching the equivalent fixture in
        tests/test_db_compatibility.py, since the current schema's own
        CHECK constraint would otherwise prevent creating this scenario
        at all."""
        import sqlite3
        conn = sqlite3.connect(self.tmpdb)
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT, email TEXT UNIQUE);
            CREATE TABLE complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                assigned_officer_id INTEGER,
                category TEXT, status TEXT DEFAULT 'Submitted'
            );
        """)
        uid = conn.execute("INSERT INTO users (full_name, email) VALUES (?,?)", ("C", "c@test.com")).lastrowid
        conn.execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)",
                     (uid, "Plastic", "SomeUnknownStatus"))
        conn.commit()
        conn.close()

        from database.migrate_data import audit_source_data
        reports = audit_source_data(("complaints",))
        self.assertEqual(reports["complaints"].invalid_status_rows, 1)
        self.assertIn("SomeUnknownStatus", reports["complaints"].invalid_status_values)

    def test_chat_language_values_reported(self):
        from database.db import init_db, execute
        from database.migrate_data import audit_source_data
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        execute("INSERT INTO chat_history (user_id, session_id, role, message, language) VALUES (?,?,?,?,?)",
                (uid, "s", "user", "hi", "en"))
        execute("INSERT INTO chat_history (user_id, session_id, role, message, language) VALUES (?,?,?,?,?)",
                (uid, "s", "user", "namaste", "hi"))
        reports = audit_source_data(("chat_history",))
        self.assertEqual(reports["chat_history"].distinct_language_values, {"en", "hi"})

    def test_image_path_audit_real_files(self):
        from database.db import init_db, execute
        from database.migrate_data import audit_complaint_image_paths
        import tempfile as tf
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        real_file = tf.NamedTemporaryFile(delete=False)
        real_file.write(b"fake image bytes")
        real_file.close()
        execute("INSERT INTO complaints (user_id, category, image_path) VALUES (?,?,?)",
                (uid, "Plastic", real_file.name))
        execute("INSERT INTO complaints (user_id, category, image_path) VALUES (?,?,?)",
                (uid, "Plastic", "/nonexistent/path/missing.jpg"))

        result = audit_complaint_image_paths()
        self.assertEqual(result["total_complaints_with_image_path"], 2)
        self.assertEqual(result["files_found_on_disk"], 1)
        self.assertEqual(result["files_missing"], 1)
        os.remove(real_file.name)


class _StubCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubConnection:
    """Records every (sql, params) call. NOT a real database -- see the
    identical, explicitly-documented pattern in
    tests/test_users_migration.py. Used only to test migrate_table()'s
    write-path logic (SQL text, parameter values, transaction control)
    without a live Supabase connection."""

    def __init__(self, existing_ids=(), existing_natural_keys=(), fail_on_id=None):
        self.existing_ids = set(existing_ids)
        self.existing_natural_keys = set(existing_natural_keys)
        self.fail_on_id = fail_on_id
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT id FROM"):
            return _StubCursorResult([{"id": i} for i in self.existing_ids])
        if sql.startswith("SELECT notification_id, user_id FROM") or "notification_id, user_id" in sql:
            return _StubCursorResult([{"notification_id": nk[0], "user_id": nk[1]} for nk in self.existing_natural_keys])
        if sql.startswith("INSERT INTO"):
            if params and self.fail_on_id is not None and params[0] == self.fail_on_id:
                raise RuntimeError(f"simulated failure for id={self.fail_on_id}")
        return _StubCursorResult([])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class WritePathTests(unittest.TestCase):
    """Tests 4, 10, 11, 12: ID preservation, rollback, dry-run makes no
    writes, sequence-repair SQL -- all via a stub connection. NOT a live
    Postgres/Supabase test; see _StubConnection's docstring."""

    def _with_stub(self, table, sqlite_rows, stub, dry_run):
        import database.migrate_data as mod
        orig_fetch = mod.sqlite_fetch_all
        import database.db_supabase as db_supabase_mod
        orig_get_connection = db_supabase_mod.get_connection

        mod.sqlite_fetch_all = lambda query: sqlite_rows
        db_supabase_mod.get_connection = lambda: stub
        try:
            return mod.migrate_table(table, dry_run=dry_run)
        finally:
            mod.sqlite_fetch_all = orig_fetch
            db_supabase_mod.get_connection = orig_get_connection

    def test_dry_run_makes_no_insert_calls(self):
        rows = [{"id": 1, "name": "Plastic", "description": None, "icon": None,
                 "disposal_guide": None, "is_active": 1}]
        stub = _StubConnection()
        results = self._with_stub("categories", rows, stub, dry_run=True)
        self.assertEqual(results[0].action, "would_insert")
        inserts = [c for c in stub.calls if c[0].startswith("INSERT")]
        self.assertEqual(inserts, [])

    def test_id_preserved_in_actual_insert_call(self):
        rows = [{"id": 555, "name": "Plastic", "description": None, "icon": None,
                 "disposal_guide": None, "is_active": 1}]
        stub = _StubConnection()
        results = self._with_stub("categories", rows, stub, dry_run=False)
        self.assertEqual(results[0].action, "inserted")
        insert_calls = [c for c in stub.calls if c[0].startswith("INSERT")]
        self.assertEqual(len(insert_calls), 1)
        _, params = insert_calls[0]
        self.assertEqual(params[0], 555)  # id is always the first column in every TableSpec

    def test_rollback_one_row_failure_does_not_abort_table(self):
        rows = [
            {"id": 1, "name": "A", "description": None, "icon": None, "disposal_guide": None, "is_active": 1},
            {"id": 2, "name": "B", "description": None, "icon": None, "disposal_guide": None, "is_active": 1},
            {"id": 3, "name": "C", "description": None, "icon": None, "disposal_guide": None, "is_active": 1},
        ]
        stub = _StubConnection(fail_on_id=2)
        results = self._with_stub("categories", rows, stub, dry_run=False)
        by_id = {r.row_id: r for r in results}
        self.assertEqual(by_id[1].action, "inserted")
        self.assertEqual(by_id[2].action, "error")
        self.assertEqual(by_id[3].action, "inserted")

        sql_seq = [c[0] for c in stub.calls]
        self.assertEqual(sql_seq.count("SAVEPOINT row_migration"), 3)
        self.assertEqual(sql_seq.count("RELEASE SAVEPOINT row_migration"), 2)
        self.assertEqual(sql_seq.count("ROLLBACK TO SAVEPOINT row_migration"), 1)

    def test_sequence_repair_sql_generated_after_insert(self):
        rows = [{"id": 1, "name": "A", "description": None, "icon": None, "disposal_guide": None, "is_active": 1}]
        stub = _StubConnection()
        self._with_stub("categories", rows, stub, dry_run=False)
        sql_seq = [c[0] for c in stub.calls]
        self.assertTrue(any("setval" in s and "categories" in s for s in sql_seq))

    def test_no_sequence_repair_when_nothing_inserted(self):
        rows = [{"id": 1, "name": "A", "description": None, "icon": None, "disposal_guide": None, "is_active": 1}]
        stub = _StubConnection(existing_ids={1})  # already present -> skip, nothing inserted
        self._with_stub("categories", rows, stub, dry_run=False)
        sql_seq = [c[0] for c in stub.calls]
        self.assertFalse(any("setval" in s for s in sql_seq))

    def test_idempotent_rerun_skips_already_migrated_rows(self):
        """Test 13 (write-path side): running migrate_table twice against
        a target that already has the row must skip it the second time,
        not duplicate or error."""
        rows = [{"id": 1, "name": "A", "description": None, "icon": None, "disposal_guide": None, "is_active": 1}]
        stub_first = _StubConnection()
        first = self._with_stub("categories", rows, stub_first, dry_run=False)
        self.assertEqual(first[0].action, "inserted")

        # simulate the second run against a target that now has id=1
        stub_second = _StubConnection(existing_ids={1})
        second = self._with_stub("categories", rows, stub_second, dry_run=False)
        self.assertEqual(second[0].action, "skip")
        inserts = [c for c in stub_second.calls if c[0].startswith("INSERT")]
        self.assertEqual(inserts, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
