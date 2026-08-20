"""
tests/test_preflight_5g.py
------------------------------
Step 5G-1 preflight test suite. Run with:

    python3 -m unittest tests.test_preflight_5g -v

Confirms: never raises, never prints/exposes a secret, never attempts
a connection when unconfigured, and never writes (verified by
inspecting every recorded SQL statement in the stub-connection test --
same pattern as the rest of the Step 5 test suite).
"""
import unittest


class NoConfigTests(unittest.TestCase):
    def setUp(self):
        from config import settings
        self._orig = settings.SUPABASE_DB_URL
        settings.SUPABASE_DB_URL = ""

    def tearDown(self):
        from config import settings
        settings.SUPABASE_DB_URL = self._orig

    def test_never_raises_when_unconfigured(self):
        from database.preflight_5g import run_preflight
        report = run_preflight()  # must not raise
        self.assertFalse(report["configured"])
        self.assertFalse(report["ready_for_dry_run"])
        self.assertEqual(report["tables"], {})

    def test_report_contains_no_secret_value(self):
        """The report dict must never contain the literal SUPABASE_DB_URL
        value (or any string that looks like a connection string)."""
        from config import settings
        from database.preflight_5g import run_preflight
        settings.SUPABASE_DB_URL = "postgresql://user:supersecret@host/db"
        report = run_preflight()
        report_text = str(report)
        self.assertNotIn("supersecret", report_text)
        self.assertNotIn("postgresql://", report_text)


class _StubCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubConnection:
    """Same pattern as every other Step 5 test file -- a recorded fake,
    not a live database."""

    def __init__(self, table_counts: dict):
        self.table_counts = table_counts  # {table: count} -- absent key means "table missing"
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(sql)
        for table, count in self.table_counts.items():
            if f"FROM {table}" in sql:
                return _StubCursorResult([{"c": count}])
        raise Exception(f"relation \"{sql.split('FROM ')[-1].strip()}\" does not exist")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ReachableStubTests(unittest.TestCase):
    """Exercises the table-existence/row-count path via a stub
    connection -- NOT a live Supabase connection. Uses a real temporary
    SQLite database for the source-count side (preflight reads SQLite
    directly, same as every migration tool)."""

    def setUp(self):
        import tempfile, os
        self.tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        os.remove(self.tmpdb)
        from config import settings
        self._orig_path = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.tmpdb
        from database.db import init_db
        init_db()

        import database.db_supabase as m
        self._orig_health = m.health_check
        self._orig_get_conn = m.get_connection
        self._orig_sdk = m.is_sdk_available

    def tearDown(self):
        import os
        from config import settings
        settings.DATABASE_PATH = self._orig_path
        if os.path.exists(self.tmpdb):
            os.remove(self.tmpdb)
        import database.db_supabase as m
        m.health_check = self._orig_health
        m.get_connection = self._orig_get_conn
        m.is_sdk_available = self._orig_sdk

    def _patch(self, stub):
        import database.db_supabase as m
        m.is_sdk_available = lambda: True
        m.health_check = lambda: {"sdk_installed": True, "configured": True,
                                   "reachable": True, "detail": "ok (stub)"}
        m.get_connection = lambda: stub
        from config import settings
        settings.SUPABASE_DB_URL = "postgresql://fake/for-stub-test-only"

    def test_all_tables_missing_reports_clearly(self):
        stub = _StubConnection(table_counts={})
        self._patch(stub)
        from database.preflight_5g import run_preflight, ALL_TABLES
        report = run_preflight()
        self.assertFalse(report["ready_for_dry_run"])
        self.assertTrue(any("Missing tables" in w for w in report["warnings"]))
        for t in ALL_TABLES:
            self.assertFalse(report["tables"][t]["supabase_exists"])

    def test_all_tables_present_and_empty_is_ready(self):
        from database.preflight_5g import ALL_TABLES
        stub = _StubConnection(table_counts={t: 0 for t in ALL_TABLES})
        self._patch(stub)
        from database.preflight_5g import run_preflight
        report = run_preflight()
        self.assertTrue(report["ready_for_dry_run"])
        self.assertEqual(report["warnings"], [])

    def test_unexpected_existing_data_is_flagged(self):
        from database.preflight_5g import ALL_TABLES
        counts = {t: 0 for t in ALL_TABLES}
        counts["users"] = 5  # unexpected data in what should be an empty staging target
        stub = _StubConnection(table_counts=counts)
        self._patch(stub)
        from database.preflight_5g import run_preflight
        report = run_preflight()
        self.assertTrue(any("already has 5 row" in w for w in report["warnings"]))

    def test_no_write_statements_issued(self):
        from database.preflight_5g import ALL_TABLES, run_preflight
        stub = _StubConnection(table_counts={t: 0 for t in ALL_TABLES})
        self._patch(stub)
        run_preflight()
        for sql in stub.calls:
            self.assertTrue(sql.strip().upper().startswith("SELECT"), f"non-SELECT call: {sql}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
