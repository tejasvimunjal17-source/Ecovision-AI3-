"""
tests/test_db_compatibility.py
---------------------------------
Step 5D-1 compatibility test suite. Run with:

    python3 -m unittest tests.test_db_compatibility -v

Two groups of tests, clearly separated (see each class's docstring):

1. SQLiteRegressionTests / ComplaintsStatusMigrationTests -- run
   against a REAL temporary SQLite database. These exercise the
   application's actual, unchanged SQLite behavior (USE_SUPABASE stays
   false throughout) to catch any regression from the Step 5 work.

2. SqlDialectTests / DbSupabaseAdapterTests / DbRouterDispatchTests --
   exercise the new, not-yet-adopted Step 5A/5C/5D-1 modules in
   isolation. Where a query fragment can be verified by actually
   running it (the SQLite-dialect branch of database/sql_dialect.py),
   this suite does so for real. Where it can't (anything requiring a
   live Postgres/Supabase connection), the test only checks
   configuration/error-handling behavior and is explicitly commented
   as such -- never faked as a "passing" live-connection test.

No test here ever sets USE_SUPABASE=true against the real environment
(the router-dispatch tests spawn an isolated subprocess with the env
var set, so the flag's real default is never touched for the rest of
this process or any other test).
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class _TempSqliteDbMixin:
    """Points config.settings.DATABASE_PATH at a fresh temp file for the
    duration of one test, and cleans up afterwards. Every test that
    touches the real database.db module uses this, so tests never share
    state and never touch the developer's real database/ecovision.db."""

    def setUp(self):
        self.tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        os.remove(self.tmpdb)  # let init_db() create it fresh
        from config import settings
        self._orig_path = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.tmpdb

    def tearDown(self):
        from config import settings
        settings.DATABASE_PATH = self._orig_path
        if os.path.exists(self.tmpdb):
            os.remove(self.tmpdb)


class SQLiteRegressionTests(_TempSqliteDbMixin, unittest.TestCase):
    """
    PASS = locally tested, against a real temp SQLite database.
    Confirms every existing feature still works exactly as before the
    Step 5 preparation work, with USE_SUPABASE at its default (false).
    """

    def test_init_db_creates_all_tables(self):
        from database.db import init_db, fetch_all
        init_db()
        tables = {r["name"] for r in fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
        expected = {
            "users", "categories", "complaints", "complaint_timeline", "rewards",
            "chat_history", "recycling_centres", "carbon_records", "login_attempts",
            "audit_log", "notifications", "notification_recipients",
        }
        self.assertTrue(expected.issubset(tables), f"missing tables: {expected - tables}")

    def test_authentication_register_and_login(self):
        from database.db import init_db
        from backend.auth import register_user, login_user
        init_db()
        ok, user_id = register_user("Test Citizen", "citizen@test.com", "9999999999", "Passw0rd!")
        self.assertTrue(ok, user_id)
        ok, user = login_user("citizen@test.com", "Passw0rd!")
        self.assertTrue(ok)
        self.assertEqual(user["role"], "citizen")
        self.assertEqual(user["email"], "citizen@test.com")
        # wrong password must fail
        ok, msg = login_user("citizen@test.com", "WrongPassword!")
        self.assertFalse(ok)

    def test_google_auth_get_or_create(self):
        from database.db import init_db
        from backend.auth import get_or_create_google_user
        init_db()
        ok, user = get_or_create_google_user("googleuser@test.com", "Google User", "google-sub-123")
        self.assertTrue(ok)
        self.assertEqual(user["auth_provider"], "google")
        # second call for the same email must return the same account, not create a duplicate
        ok2, user2 = get_or_create_google_user("googleuser@test.com", "Google User", "google-sub-123")
        self.assertTrue(ok2)
        self.assertEqual(user["id"], user2["id"])

    def test_complaints_create_and_workflow(self):
        from database.db import init_db, execute, fetch_one
        from backend.complaints import create_complaint, update_status, get_user_complaints
        init_db()
        uid = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Citizen", "c2@test.com", "9", "x", "y", "citizen"),
        )
        complaint_id = create_complaint(uid, "Plastic", "Bottles dumped near park", ward="Sector 5")
        self.assertIsInstance(complaint_id, int)
        row = fetch_one("SELECT * FROM complaints WHERE id=?", (complaint_id,))
        self.assertEqual(row["status"], "Submitted")

        update_status(complaint_id, "Resolved", changed_by=uid)
        row = fetch_one("SELECT * FROM complaints WHERE id=?", (complaint_id,))
        self.assertEqual(row["status"], "Resolved")
        self.assertIsNotNone(row["resolved_at"])

        my_complaints = get_user_complaints(uid)
        self.assertEqual(len(my_complaints), 1)

    def test_rewards(self):
        from database.db import init_db, execute
        from backend.complaints import award_points, get_user_rewards
        init_db()
        uid = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Citizen", "c3@test.com", "9", "x", "y", "citizen"),
        )
        award_points(uid, 50, "Complaint resolved")
        rewards = get_user_rewards(uid)
        self.assertEqual(len(rewards), 1)
        self.assertEqual(rewards[0]["points"], 50)

    def test_prakriti_chat_history(self):
        from database.db import init_db, execute
        from chatbot.prakriti import save_message, load_history, clear_history
        init_db()
        uid = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Citizen", "c4@test.com", "9", "x", "y", "citizen"),
        )
        save_message(uid, "sess1", "user", "How do I segregate waste?", "en")
        save_message(uid, "sess1", "assistant", "Separate wet and dry waste...", "en")
        history = load_history(uid, "sess1")
        self.assertEqual(len(history), 2)
        clear_history(uid, "sess1")
        self.assertEqual(load_history(uid, "sess1"), [])

    def test_notifications(self):
        from database.db import init_db, execute
        from backend.notifications import send_notification, get_unread_count, mark_all_read
        init_db()
        uid_citizen = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Citizen", "c5@test.com", "9", "x", "y", "citizen"),
        )
        uid_admin = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Admin", "a5@test.com", "9", "x", "y", "admin"),
        )
        ok, count = send_notification("Hi", "msg", created_by=uid_admin, audience="citizens")
        self.assertTrue(ok)
        self.assertEqual(count, 1)
        self.assertEqual(get_unread_count(uid_citizen), 1)
        mark_all_read(uid_citizen)
        self.assertEqual(get_unread_count(uid_citizen), 0)

    def test_direct_page_queries_categories_and_recycling_centres(self):
        """Covers pages/10 and pages/13's direct fetch_all() calls."""
        from database.db import init_db, fetch_all
        init_db()
        categories = fetch_all("SELECT * FROM categories WHERE is_active=1")
        self.assertGreater(len(categories), 0)
        centres = fetch_all("SELECT * FROM recycling_centres WHERE is_active=1")
        self.assertGreater(len(centres), 0)

    def test_direct_page_query_carbon_records_insert(self):
        """Covers pages/11's direct execute() call."""
        from database.db import init_db, execute, fetch_all
        init_db()
        uid = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Citizen", "c6@test.com", "9", "x", "y", "citizen"),
        )
        execute(
            """INSERT INTO carbon_records (user_id, transport_kg, electricity_kg, plastic_kg, water_kg,
               food_kg, waste_kg, total_score) VALUES (?,?,?,?,?,?,?,?)""",
            (uid, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 21.0),
        )
        records = fetch_all("SELECT * FROM carbon_records WHERE user_id=?", (uid,))
        self.assertEqual(len(records), 1)


class ComplaintsStatusMigrationTests(_TempSqliteDbMixin, unittest.TestCase):
    """PASS = locally tested. The complaints.status CHECK-constraint
    migration (Step 5B follow-up, implemented in this step)."""

    def _build_legacy_db(self):
        """Hand-builds a pre-Step-5D-1 database: complaints.status has
        NO check constraint, mimicking a real production DB that
        predates this migration."""
        conn = sqlite3.connect(self.tmpdb)
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE, phone TEXT, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'citizen', auth_provider TEXT NOT NULL DEFAULT 'email',
                google_id TEXT UNIQUE, ward TEXT, address TEXT, avatar_path TEXT, security_question TEXT,
                security_answer_hash TEXT, reward_points INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT (datetime('now')), last_login TEXT);
            CREATE TABLE complaints (id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, category TEXT NOT NULL,
                ai_predicted_category TEXT, ai_confidence REAL, description TEXT, ai_description TEXT,
                priority TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Submitted', image_path TEXT,
                latitude REAL, longitude REAL, ward TEXT, address_text TEXT,
                assigned_officer_id INTEGER REFERENCES users(id), assigned_worker TEXT, officer_notes TEXT,
                created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT);
            CREATE TABLE complaint_timeline (id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
                status TEXT NOT NULL, note TEXT, changed_by INTEGER REFERENCES users(id),
                created_at TEXT DEFAULT (datetime('now')));
            CREATE INDEX idx_complaints_user ON complaints(user_id);
            CREATE INDEX idx_complaints_status ON complaints(status);
            CREATE INDEX idx_complaints_ward ON complaints(ward);
        """)
        return conn

    def test_fresh_database_gets_constraint_from_schema(self):
        from database.db import init_db, fetch_one
        init_db()
        row = fetch_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='complaints'")
        self.assertIn("CHECK (status IN", row["sql"])

    def test_constraint_actually_rejects_bad_status(self):
        from database.db import init_db, execute
        init_db()
        uid = execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("T", "t1@test.com", "9", "x", "y", "citizen"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)",
                    (uid, "Plastic", "NotARealStatus"))

    def test_migration_adds_constraint_and_preserves_data(self):
        conn = self._build_legacy_db()
        uid = conn.execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("Old User", "old@test.com", "9", "x", "y", "citizen"),
        ).lastrowid
        cid = conn.execute(
            "INSERT INTO complaints (user_id, category, status, ward) VALUES (?,?,?,?)",
            (uid, "Plastic", "Resolved", "Sector 5"),
        ).lastrowid
        conn.execute("INSERT INTO complaint_timeline (complaint_id, status, note) VALUES (?,?,?)",
                     (cid, "Resolved", "Cleaned up"))
        conn.commit()
        conn.close()

        from database.db import init_db, fetch_all, fetch_one
        init_db()

        row = fetch_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='complaints'")
        self.assertIn("CHECK (status IN", row["sql"])

        complaints = fetch_all("SELECT * FROM complaints")
        self.assertEqual(len(complaints), 1)
        self.assertEqual(complaints[0]["status"], "Resolved")

        timeline = fetch_all("SELECT * FROM complaint_timeline")
        self.assertEqual(len(timeline), 1)

        fk_violations = fetch_all("PRAGMA foreign_key_check")
        self.assertEqual(fk_violations, [])

        idx = {r["name"] for r in fetch_all(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='complaints'")}
        self.assertTrue({"idx_complaints_user", "idx_complaints_status", "idx_complaints_ward"} <= idx)

        # idempotency: running init_db() again must be a clean no-op
        init_db()
        complaints2 = fetch_all("SELECT * FROM complaints")
        self.assertEqual(len(complaints2), 1)

    def test_migration_skips_when_invalid_data_present(self):
        conn = self._build_legacy_db()
        uid = conn.execute(
            "INSERT INTO users (full_name,email,phone,password_hash,salt,role) VALUES (?,?,?,?,?,?)",
            ("U", "u@test.com", "9", "x", "y", "citizen"),
        ).lastrowid
        conn.execute("INSERT INTO complaints (user_id, category, status) VALUES (?,?,?)",
                     (uid, "Plastic", "Closed"))  # not in the allowed set
        conn.commit()
        conn.close()

        from database.db import init_db, fetch_one, fetch_all
        init_db()  # must not raise

        row = fetch_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='complaints'")
        self.assertNotIn("CHECK (status IN", row["sql"])  # migration correctly skipped
        complaints = fetch_all("SELECT status FROM complaints")
        self.assertEqual(complaints[0]["status"], "Closed")  # original data untouched


class SqlDialectTests(unittest.TestCase):
    """PASS = locally tested. SQLite-dialect branches are verified by
    actually running them against a real (in-memory) SQLite database.
    Postgres-dialect branches are text-verified only -- no live
    Postgres/Supabase connection is available, and this suite does not
    pretend otherwise."""

    def test_pure_function_outputs_both_dialects(self):
        from database import sql_dialect as d
        self.assertEqual(d.now_expr("sqlite"), "datetime('now')")
        self.assertEqual(d.now_expr("postgres"), "now()")
        self.assertEqual(d.date_expr("created_at", "sqlite"), "date(created_at)")
        self.assertEqual(d.date_expr("created_at", "postgres"), "created_at::date")
        self.assertEqual(d.month_trunc_expr("c", "sqlite"), "strftime('%Y-%m', c)")
        self.assertEqual(d.month_trunc_expr("c", "postgres"), "to_char(c, 'YYYY-MM')")
        self.assertEqual(d.now_minus_days_expr(30, "sqlite"), "datetime('now', '-30 days')")
        self.assertEqual(d.now_minus_days_expr(30, "postgres"), "now() - interval '30 days'")

    def test_invalid_backend_rejected(self):
        from database import sql_dialect as d
        with self.assertRaises(ValueError):
            d.now_expr("mysql")

    def test_sqlite_fragments_actually_execute(self):
        from database import sql_dialect as d
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (created_at TEXT, resolved_at TEXT)")
        conn.execute(f"INSERT INTO t (created_at) VALUES ({d.now_expr('sqlite')})")
        conn.execute("UPDATE t SET resolved_at = datetime(created_at, '+2 hours')")

        row = conn.execute(f"SELECT {d.age_hours_expr('created_at','resolved_at','sqlite')} FROM t").fetchone()
        self.assertAlmostEqual(row[0], 2.0, places=1)

        row = conn.execute(f"SELECT {d.date_expr('created_at','sqlite')} FROM t").fetchone()
        self.assertIsNotNone(row[0])

        row = conn.execute(f"SELECT {d.month_trunc_expr('created_at','sqlite')} FROM t").fetchone()
        self.assertIsNotNone(row[0])
        conn.close()

    def test_insert_ignore_sql_sqlite_branch_executes_and_is_idempotent(self):
        from database import sql_dialect as d
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE cats (name TEXT UNIQUE, icon TEXT)")
        sql = d.insert_ignore_sql("cats", ["name", "icon"], ["name"], backend="sqlite")
        self.assertEqual(sql, "INSERT OR IGNORE INTO cats (name, icon) VALUES (?, ?)")
        conn.execute(sql, ("Plastic", "🧴"))
        conn.execute(sql, ("Plastic", "🧴"))  # duplicate -- must be silently ignored
        count = conn.execute("SELECT COUNT(*) FROM cats").fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()

    def test_insert_ignore_sql_postgres_branch_text_only(self):
        """NOT TESTED against a live Postgres connection -- text/syntax
        review only, per this step's explicit instruction not to fake a
        successful database test."""
        from database import sql_dialect as d
        sql = d.insert_ignore_sql("cats", ["name", "icon"], ["name"], backend="postgres")
        self.assertEqual(sql, "INSERT INTO cats (name, icon) VALUES (?, ?) ON CONFLICT (name) DO NOTHING")

    def test_failed_attempts_since_param_types(self):
        import datetime
        from database import sql_dialect as d
        self.assertIsInstance(d.failed_attempts_since_param(15, "sqlite"), str)
        self.assertIsInstance(d.failed_attempts_since_param(15, "postgres"), datetime.datetime)


class DbSupabaseAdapterTests(unittest.TestCase):
    """
    PASS = locally tested (configuration/error-handling behavior only).
    NOT TESTED = anything requiring an actual live Supabase/Postgres
    connection -- explicitly skipped/marked, never faked.
    """

    def setUp(self):
        from config import settings
        self._orig_db_url = settings.SUPABASE_DB_URL
        settings.SUPABASE_DB_URL = ""  # ensure a clean "not configured" state for these tests

    def tearDown(self):
        from config import settings
        settings.SUPABASE_DB_URL = self._orig_db_url

    def test_placeholder_translation(self):
        from database import db_supabase as sb
        self.assertEqual(
            sb._translate_placeholders("SELECT * FROM t WHERE a=? AND b=?"),
            "SELECT * FROM t WHERE a=%s AND b=%s",
        )

    def test_insert_detection_regex(self):
        from database import db_supabase as sb
        self.assertTrue(sb._INSERT_RE.match("INSERT INTO users (a) VALUES (?)"))
        self.assertFalse(sb._INSERT_RE.match("INSERT OR IGNORE INTO cats (a) VALUES (?)"))
        self.assertFalse(sb._INSERT_RE.match("UPDATE users SET a=? WHERE id=?"))
        self.assertFalse(sb._INSERT_RE.match("DELETE FROM users WHERE id=?"))
        self.assertTrue(sb._RETURNING_RE.search("INSERT INTO t (a) VALUES (?) RETURNING id"))
        self.assertFalse(sb._RETURNING_RE.search("INSERT INTO t (a) VALUES (?)"))

    def test_no_config_raises_clearly_not_none(self):
        from database import db_supabase as sb
        for fn in (lambda: sb.execute("SELECT 1"),
                   lambda: sb.fetch_one("SELECT 1"),
                   lambda: sb.fetch_all("SELECT 1"),
                   lambda: sb.init_db()):
            with self.assertRaises(sb.SupabaseAdapterError):
                fn()

    def test_health_check_never_raises_when_unconfigured(self):
        from database import db_supabase as sb
        result = sb.health_check()  # must not raise
        self.assertFalse(result["configured"])
        self.assertFalse(result["reachable"])
        self.assertIn("detail", result)


class DbRouterDispatchTests(unittest.TestCase):
    """
    PASS = locally tested. Runs the router in an isolated subprocess per
    case (USE_SUPABASE reads its value at db_router's import time, so a
    fresh process is the faithful way to test both branches -- matching
    how a real Streamlit process boots with one fixed value for its
    whole lifetime).
    """

    def _run(self, code: str, env_extra: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(env_extra)
        return subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                               env=env, capture_output=True, text=True, timeout=30)

    def test_default_use_supabase_false_routes_to_sqlite(self):
        result = self._run(
            "from database.db_router import active_backend; print(active_backend())",
            env_extra={},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "sqlite")

    def test_use_supabase_true_routes_to_supabase_not_sqlite(self):
        result = self._run(
            "from database.db_router import active_backend; print(active_backend())",
            env_extra={"USE_SUPABASE": "true"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "supabase")

    def test_use_supabase_true_with_no_credentials_raises_not_silent_sqlite(self):
        """The critical no-silent-fallback guarantee: USE_SUPABASE=true
        with no Supabase credentials must raise, not quietly return
        SQLite data."""
        result = self._run(
            "from database.db_router import execute\n"
            "try:\n"
            "    execute('SELECT 1')\n"
            "    print('SILENTLY-SUCCEEDED')\n"
            "except Exception as e:\n"
            "    print(f'RAISED:{type(e).__name__}')\n",
            env_extra={"USE_SUPABASE": "true"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RAISED:", result.stdout)
        self.assertNotIn("SILENTLY-SUCCEEDED", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
