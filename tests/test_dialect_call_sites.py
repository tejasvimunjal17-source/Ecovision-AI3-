"""
tests/test_dialect_call_sites.py
------------------------------------
Step 5F test suite. Run with:

    python3 -m unittest tests.test_dialect_call_sites -v

database/sql_dialect.py's pure functions were already unit-tested in
Step 5D-1 (tests/test_db_compatibility.py::SqlDialectTests). THIS file
is about the six real call sites that now use them
(backend/auth.py x2, backend/complaints.py x3, backend/analytics.py x3,
backend/notifications.py x3, pages/8's category form x1) -- confirming
each one still executes correctly against real SQLite (unchanged
behavior) and confirming current_dialect() actually reflects
USE_SUPABASE via a fresh subprocess (since active_backend() is
import-time state, same reasoning as Step 5E's dispatcher tests).

PostgreSQL-side generation is checked as SQL TEXT ONLY -- no live
Postgres/Supabase connection exists in this environment, and this
suite never claims otherwise.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class CurrentDialectTests(unittest.TestCase):
    """current_dialect() must reflect the router's active backend."""

    def _run(self, code, env_extra):
        env = dict(os.environ)
        env.update(env_extra)
        return subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                               env=env, capture_output=True, text=True, timeout=30)

    def test_current_dialect_sqlite_by_default(self):
        result = self._run("from database.sql_dialect import current_dialect\nprint(current_dialect())\n", {})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "sqlite")

    def test_current_dialect_postgres_when_use_supabase_true(self):
        result = self._run("from database.sql_dialect import current_dialect\nprint(current_dialect())\n",
                            {"USE_SUPABASE": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "postgres")


class AuthDialectSitesTests(_TempSqliteDbMixin, unittest.TestCase):
    """backend/auth.py: _recent_failed_attempts() (failed_attempts_since_param)
    and the two last_login UPDATEs (now_expr)."""

    def test_recent_failed_attempts_counts_correctly(self):
        from database.db_router import init_db
        from backend.auth import register_user, login_user, _recent_failed_attempts
        init_db()
        ok, uid = register_user("Test", "t@test.com", "9", "WrongPass1!")
        self.assertTrue(ok, uid)
        login_user("t@test.com", "bad1")
        login_user("t@test.com", "bad2")
        self.assertEqual(_recent_failed_attempts("t@test.com"), 2)

    def test_last_login_updated_on_successful_login(self):
        from database.db_router import init_db, fetch_one
        from backend.auth import register_user, login_user
        init_db()
        ok, uid = register_user("Test", "t2@test.com", "9", "GoodPass1!")
        self.assertTrue(ok, uid)
        before = fetch_one("SELECT last_login FROM users WHERE id=?", (uid,))["last_login"]
        self.assertIsNone(before)
        ok2, user = login_user("t2@test.com", "GoodPass1!")
        self.assertTrue(ok2, user)
        after = fetch_one("SELECT last_login FROM users WHERE id=?", (uid,))["last_login"]
        self.assertIsNotNone(after)

    def test_google_login_also_updates_last_login(self):
        from database.db_router import init_db, fetch_one
        from backend.auth import get_or_create_google_user
        init_db()
        ok, user = get_or_create_google_user("g@test.com", "Google User", "sub-1")
        self.assertTrue(ok)
        row = fetch_one("SELECT last_login FROM users WHERE id=?", (user["id"],))
        self.assertIsNotNone(row["last_login"])


class ComplaintsDialectSitesTests(_TempSqliteDbMixin, unittest.TestCase):
    """backend/complaints.py: update_status()'s two now_expr() UPDATEs,
    assign_officer()'s now_expr() UPDATE."""

    def _make_user_and_complaint(self):
        from database.db_router import init_db, execute
        from backend.complaints import create_complaint
        init_db()
        uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                      ("C", "c@test.com", "9", "x", "y", "citizen"))
        cid = create_complaint(uid, "Plastic", "desc")
        return uid, cid

    def test_update_status_sets_updated_at(self):
        from database.db_router import fetch_one
        from backend.complaints import update_status
        uid, cid = self._make_user_and_complaint()
        update_status(cid, "Under Review", changed_by=uid)
        row = fetch_one("SELECT status, updated_at FROM complaints WHERE id=?", (cid,))
        self.assertEqual(row["status"], "Under Review")
        self.assertIsNotNone(row["updated_at"])

    def test_update_status_resolved_sets_resolved_at(self):
        from database.db_router import fetch_one
        from backend.complaints import update_status
        uid, cid = self._make_user_and_complaint()
        update_status(cid, "Resolved", changed_by=uid)
        row = fetch_one("SELECT status, resolved_at FROM complaints WHERE id=?", (cid,))
        self.assertEqual(row["status"], "Resolved")
        self.assertIsNotNone(row["resolved_at"])

    def test_assign_officer_sets_updated_at_and_status(self):
        from database.db_router import fetch_one, execute
        from backend.complaints import assign_officer
        uid, cid = self._make_user_and_complaint()
        officer_id = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                             ("O", "o@test.com", "9", "x", "y", "officer"))
        assign_officer(cid, officer_id, "Worker A", changed_by=uid)
        row = fetch_one("SELECT status, assigned_officer_id, updated_at FROM complaints WHERE id=?", (cid,))
        self.assertEqual(row["status"], "Assigned")
        self.assertEqual(row["assigned_officer_id"], officer_id)
        self.assertIsNotNone(row["updated_at"])


class AnalyticsDialectSitesTests(_TempSqliteDbMixin, unittest.TestCase):
    """backend/analytics.py: kpi_summary() (age_hours_expr),
    complaints_daily_trend() (date_expr + now_minus_days_expr),
    complaints_monthly_trend() (month_trunc_expr)."""

    def setUp(self):
        super().setUp()
        from database.db_router import init_db, execute
        init_db()
        self.uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                           ("C", "c@test.com", "9", "x", "y", "citizen"))

    def test_kpi_summary_avg_resolution_hours_computes(self):
        from database.db_router import execute
        from backend import analytics
        cid = execute("INSERT INTO complaints (user_id, category, status, created_at, resolved_at) "
                      "VALUES (?,?,?,datetime('now','-3 hours'),datetime('now'))",
                      (self.uid, "Plastic", "Resolved"))
        kpis = analytics.kpi_summary()
        self.assertGreater(kpis["avg_resolution_hours"], 2.5)
        self.assertLess(kpis["avg_resolution_hours"], 3.5)

    def test_kpi_summary_zero_resolved_returns_zero_not_error(self):
        from backend import analytics
        kpis = analytics.kpi_summary()  # no complaints at all yet
        self.assertEqual(kpis["avg_resolution_hours"], 0)

    def test_complaints_daily_trend_groups_by_day(self):
        from database.db_router import execute
        from backend import analytics
        execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)",
                (self.uid, "Plastic", "Submitted"))
        trend = analytics.complaints_daily_trend(30)
        self.assertEqual(len(trend), 1)
        self.assertEqual(trend[0]["count"], 1)

    def test_complaints_daily_trend_respects_day_window(self):
        from database.db_router import execute
        from backend import analytics
        # a complaint from 90 days ago must NOT appear in a 30-day trend
        execute("INSERT INTO complaints (user_id, category, status, created_at) "
                "VALUES (?,?,?,datetime('now','-90 days'))", (self.uid, "Plastic", "Submitted"))
        trend = analytics.complaints_daily_trend(30)
        self.assertEqual(trend, [])

    def test_complaints_monthly_trend_groups_by_month(self):
        from database.db_router import execute
        from backend import analytics
        execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)",
                (self.uid, "Plastic", "Submitted"))
        trend = analytics.complaints_monthly_trend()
        self.assertEqual(len(trend), 1)
        self.assertEqual(trend[0]["count"], 1)


class NotificationsDialectSitesTests(_TempSqliteDbMixin, unittest.TestCase):
    """backend/notifications.py: send_notification()'s insert_ignore_sql()
    fan-out, mark_read()/mark_all_read()'s now_expr() UPDATEs."""

    def setUp(self):
        super().setUp()
        from database.db_router import init_db, execute
        init_db()
        self.uid = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                           ("C", "c@test.com", "9", "x", "y", "citizen"))
        self.admin_id = execute("INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
                                ("A", "a@test.com", "9", "x", "y", "admin"))

    def test_send_notification_fans_out_without_duplicate_key_error(self):
        from backend.notifications import send_notification
        ok, count = send_notification("Hi", "msg", created_by=self.admin_id, audience="all")
        self.assertTrue(ok)
        # citizen + our admin + the seeded default admin (database/db.py::_seed_admin())
        self.assertEqual(count, 3)

    def test_mark_read_sets_read_at(self):
        from database.db_router import fetch_all
        from backend.notifications import send_notification, mark_read, get_unread_count
        send_notification("Hi", "msg", created_by=self.admin_id, audience="citizens")
        self.assertEqual(get_unread_count(self.uid), 1)
        nid = fetch_all("SELECT id FROM notification_recipients WHERE user_id=?", (self.uid,))[0]["id"]
        mark_read(nid, self.uid)
        self.assertEqual(get_unread_count(self.uid), 0)

    def test_mark_all_read_clears_everything(self):
        from backend.notifications import send_notification, mark_all_read, get_unread_count
        send_notification("A", "a", created_by=self.admin_id, audience="citizens")
        send_notification("B", "b", created_by=self.admin_id, audience="citizens")
        self.assertEqual(get_unread_count(self.uid), 2)
        mark_all_read(self.uid)
        self.assertEqual(get_unread_count(self.uid), 0)


class AdminCategoryInsertDialectTests(_TempSqliteDbMixin, unittest.TestCase):
    """pages/8's add-category form: insert_ignore_sql() for `categories`."""

    def test_category_insert_and_duplicate_is_ignored(self):
        from database.db_router import init_db, execute, fetch_all
        from database.sql_dialect import current_dialect, insert_ignore_sql
        init_db()
        sql = insert_ignore_sql("categories", ["name", "description", "icon", "disposal_guide"],
                                  ["name"], current_dialect())
        execute(sql, ("Textiles", "Old clothes", "👕", "Donate"))
        execute(sql, ("Textiles", "Old clothes", "👕", "Donate"))  # duplicate name
        rows = fetch_all("SELECT * FROM categories WHERE name='Textiles'")
        self.assertEqual(len(rows), 1)


class PostgresDialectGenerationTests(unittest.TestCase):
    """
    Text/syntax verification ONLY for every affected call site's
    Postgres-dialect output. NOT TESTED against a live PostgreSQL/
    Supabase connection -- none is available in this environment, and
    this suite does not claim otherwise (see class name and every
    assertion below: string equality checks, no database.execute()
    call).
    """

    def test_auth_now_expr_and_since_param(self):
        from database.sql_dialect import now_expr, failed_attempts_since_param, POSTGRES
        import datetime
        self.assertEqual(now_expr(POSTGRES), "now()")
        self.assertIsInstance(failed_attempts_since_param(15, POSTGRES), datetime.datetime)

    def test_complaints_now_expr(self):
        from database.sql_dialect import now_expr, POSTGRES
        self.assertEqual(now_expr(POSTGRES), "now()")

    def test_analytics_age_hours_date_month_trunc(self):
        from database.sql_dialect import age_hours_expr, date_expr, month_trunc_expr, now_minus_days_expr, POSTGRES
        self.assertEqual(age_hours_expr("created_at", "resolved_at", POSTGRES),
                          "EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600")
        self.assertEqual(date_expr("created_at", POSTGRES), "created_at::date")
        self.assertEqual(month_trunc_expr("created_at", POSTGRES), "to_char(created_at, 'YYYY-MM')")
        self.assertEqual(now_minus_days_expr(30, POSTGRES), "now() - interval '30 days'")

    def test_notifications_insert_ignore_becomes_on_conflict(self):
        from database.sql_dialect import insert_ignore_sql, POSTGRES
        sql = insert_ignore_sql("notification_recipients", ["notification_id", "user_id"],
                                  ["notification_id", "user_id"], POSTGRES)
        self.assertEqual(sql, "INSERT INTO notification_recipients (notification_id, user_id) "
                               "VALUES (?, ?) ON CONFLICT (notification_id, user_id) DO NOTHING")

    def test_admin_category_insert_ignore_becomes_on_conflict(self):
        from database.sql_dialect import insert_ignore_sql, POSTGRES
        sql = insert_ignore_sql("categories", ["name", "description", "icon", "disposal_guide"],
                                  ["name"], POSTGRES)
        self.assertEqual(sql, "INSERT INTO categories (name, description, icon, disposal_guide) "
                               "VALUES (?, ?, ?, ?) ON CONFLICT (name) DO NOTHING")


class RemainingSqliteSqlScanTests(unittest.TestCase):
    """
    Repository-wide scan (Step 5F item 3): confirms no unfixed
    SQLite-specific SQL STRING remains in application code paths that
    could run under USE_SUPABASE=true. Distinguishes SQL strings from
    legitimate Python-only datetime/strftime calls by checking for the
    SQL-only markers (a bare "datetime(" call is fine in Python; the
    SQL forms always appear inside a query string passed to execute/
    fetch_one/fetch_all, recognizable by the surrounding SQL keywords).
    """

    ALLOWED_FILES = {
        "database/db.py",              # SQLite-only backend implementation -- by design, see its own module docstring
        "database/migrate_data.py",    # reads SQLite as the migration SOURCE, not the live app path
        "database/sql_dialect.py",     # contains the SQLite-dialect STRINGS as return values, not live queries
        "database/db_supabase.py",     # mentions these constructs only in its own docstring, documenting what it deliberately does NOT auto-translate -- not a live query
    }

    def test_no_sql_datetime_now_outside_allowed_files(self):
        import re
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("tests/") or "__pycache__" in rel or rel in self.ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"datetime\('now'\)", text):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_no_sql_julianday_strftime_date_outside_allowed_files(self):
        import re
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("tests/") or "__pycache__" in rel or rel in self.ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            # SQL forms: julianday(, strftime( immediately followed by a
            # SQL format string like '%Y-%m', or SQL date( wrapping a
            # column name -- distinguished from Python's
            # datetime_obj.strftime("...") by requiring the call NOT be
            # preceded by a "." (a method call on a Python object).
            if re.search(r"(?<!\.)\bjulianday\(", text):
                offenders.append((rel, "julianday("))
            if re.search(r"(?<!\.)\bstrftime\('%Y-%m'", text):
                offenders.append((rel, "strftime SQL form"))
            if re.search(r"SELECT\s+date\(", text) or re.search(r"\bdate\(created_at\)", text):
                offenders.append((rel, "date( SQL form"))
        self.assertEqual(offenders, [])

    def test_no_insert_or_ignore_outside_allowed_files(self):
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("tests/") or "__pycache__" in rel or rel in self.ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            if "INSERT OR IGNORE" in text:
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_legitimate_python_strftime_calls_still_present_and_untouched(self):
        """Sanity check the other direction: confirms this scan isn't
        overzealous -- pure-Python datetime formatting (filenames,
        session ids, display strings) must still be present, unflagged,
        exactly as before Step 5F."""
        text = (REPO_ROOT / "utils/helpers.py").read_text(encoding="utf-8")
        self.assertIn('datetime.utcnow().strftime("%Y%m%d%H%M%S")', text)
        text2 = (REPO_ROOT / "pages/4_📢_Report_Waste.py").read_text(encoding="utf-8")
        self.assertIn('datetime.utcnow().strftime("%Y%m%d%H%M%S")', text2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
