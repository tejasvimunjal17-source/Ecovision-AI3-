"""
tests/test_dispatcher_wiring.py
-----------------------------------
Step 5E test suite. Run with:

    python3 -m unittest tests.test_dispatcher_wiring -v

database/db_router.py itself was already tested in Step 5C
(tests/test_db_compatibility.py::DbRouterDispatchTests). THIS file is
about the wiring, not the router: does the real application code
(backend/auth.py, backend/complaints.py, backend/analytics.py,
backend/notifications.py, chatbot/prakriti.py, and the 4 pages with
direct queries) actually go through database.db_router now, and does
switching USE_SUPABASE change what backend those modules use --
without ever silently falling back to SQLite when Supabase is
requested but unavailable.

Two techniques, matching the rest of this test suite:
  - Import-source verification (no I/O): confirms every relevant file's
    import statement was actually changed, by reading the file text --
    catches an accidental missed file or a typo import path.
  - Subprocess-based dispatch tests (fresh process per case, since
    USE_SUPABASE is read at db_router's *import* time): confirms an
    application module (backend.auth, chosen as representative -- every
    swapped file uses the identical import pattern) actually resolves
    its execute()/fetch_one()/etc. to the correct backend depending on
    the environment variable, and that USE_SUPABASE=true fails loudly
    rather than quietly using SQLite.
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every file Step 5E was supposed to change, and the module it must now
# import execute()/fetch_one()/etc. from.
ROUTED_FILES = (
    "app.py",
    "utils/helpers.py",
    "backend/auth.py",
    "backend/complaints.py",
    "backend/analytics.py",
    "backend/notifications.py",
    "chatbot/prakriti.py",
    "pages/8_🛠️_Admin_Dashboard.py",
    "pages/10_♻️_Recycling_Guide.py",
    "pages/11_🌍_Carbon_Calculator.py",
    "pages/13_📍_Recycling_Centres.py",
)

# database/db_router.py itself must keep importing the real
# database.db module (that's its "false" branch) -- and the standalone
# migration/verification tools must keep reading straight from SQLite
# (they migrate/verify the source of truth, not whatever the live app
# happens to be pointed at). These are the ONLY files allowed to still
# import database.db directly.
ALLOWED_DIRECT_DB_IMPORTERS = {
    "database/db_router.py",
    "database/migrate_users.py",
    "database/migrate_data.py",
    "database/verify_migration.py",
    "database/preflight_5g.py",
}

_DIRECT_IMPORT_RE = re.compile(r"^\s*from database\.db import\b", re.MULTILINE)
_ROUTER_IMPORT_RE = re.compile(r"^\s*from database\.db_router import\b", re.MULTILINE)


class ImportSourceTests(unittest.TestCase):
    """Confirms every file Step 5E was supposed to change actually was,
    by reading the file text -- not by importing it (avoids any
    import-time side effects), and not by trusting the diff, since a
    missed file would silently keep using SQLite forever even with
    USE_SUPABASE=true."""

    def test_every_routed_file_imports_from_db_router(self):
        for rel_path in ROUTED_FILES:
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            with self.subTest(file=rel_path):
                self.assertRegex(text, _ROUTER_IMPORT_RE,
                    f"{rel_path} does not import from database.db_router")

    def test_no_routed_file_still_imports_database_db_directly(self):
        for rel_path in ROUTED_FILES:
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            with self.subTest(file=rel_path):
                self.assertNotRegex(text, _DIRECT_IMPORT_RE,
                    f"{rel_path} still imports directly from database.db")

    def test_repo_wide_scan_only_allowed_files_import_database_db_directly(self):
        """Broader net than the fixed ROUTED_FILES list above -- walks
        every .py file in the repo (excluding tests/ and __pycache__)
        and fails if anything unexpected still imports database.db
        directly. Catches a file the original audit might have missed."""
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("tests/") or "__pycache__" in rel:
                continue
            text = path.read_text(encoding="utf-8")
            if _DIRECT_IMPORT_RE.search(text) and rel not in ALLOWED_DIRECT_DB_IMPORTERS:
                offenders.append(rel)
        self.assertEqual(offenders, [], f"unexpected direct database.db imports in: {offenders}")


class SubprocessDispatchTests(unittest.TestCase):
    """
    Fresh-process tests (USE_SUPABASE is read at db_router's import
    time, so a fresh process per case is the faithful way to test both
    branches -- same technique already used in Step 5C's
    DbRouterDispatchTests, applied here to an actual application module
    instead of db_router directly).
    """

    def _run(self, code: str, env_extra: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(env_extra)
        return subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                               env=env, capture_output=True, text=True, timeout=30)

    def test_backend_auth_uses_sqlite_backend_by_default(self):
        """backend.auth (representative of every swapped module -- they
        all use the identical `from database.db_router import ...`
        pattern) resolves to the SQLite implementation when
        USE_SUPABASE is unset."""
        result = self._run(
            "import backend.auth as m\n"
            "from database.db_router import active_backend\n"
            "print(active_backend())\n"
            "print(m.execute.__module__)\n",
            env_extra={},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], "sqlite")
        self.assertEqual(lines[1], "database.db")  # execute() actually came from the sqlite module

    def test_backend_auth_uses_supabase_backend_when_flag_set(self):
        result = self._run(
            "import backend.auth as m\n"
            "from database.db_router import active_backend\n"
            "print(active_backend())\n"
            "print(m.execute.__module__)\n",
            env_extra={"USE_SUPABASE": "true"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], "supabase")
        self.assertEqual(lines[1], "database.db_supabase")

    def test_backend_auth_use_supabase_true_no_credentials_raises_not_silent_sqlite(self):
        """The critical guarantee, exercised through the real
        application import path (backend.auth), not just db_router in
        isolation: USE_SUPABASE=true with no Supabase credentials must
        raise, never quietly return SQLite data."""
        result = self._run(
            "import backend.auth as m\n"
            "try:\n"
            "    m.execute('SELECT 1')\n"
            "    print('SILENTLY-SUCCEEDED')\n"
            "except Exception as e:\n"
            "    print(f'RAISED:{type(e).__name__}')\n",
            env_extra={"USE_SUPABASE": "true"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RAISED:SupabaseAdapterError", result.stdout)
        self.assertNotIn("SILENTLY-SUCCEEDED", result.stdout)

    def test_chatbot_prakriti_module_also_routes_correctly(self):
        """Spot-checks a second swapped module (not just backend.auth)
        to catch a per-file mistake in the mechanical import swap."""
        result = self._run(
            "import chatbot.prakriti as m\n"
            "print(m.execute.__module__)\n",
            env_extra={},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "database.db")

    def test_default_sqlite_full_app_flow_still_works_end_to_end(self):
        """A real, complete regression check run in a fresh process
        through the NEW import path end-to-end: init the DB, register,
        log in -- exactly what a user does -- with USE_SUPABASE unset."""
        code = (
            "import tempfile, os\n"
            "tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False).name\n"
            "os.remove(tmpdb)\n"
            "os.environ['DATABASE_PATH'] = tmpdb\n"
            "from config import settings\n"
            "settings.DATABASE_PATH = tmpdb\n"
            "from database.db_router import init_db\n"
            "init_db()\n"
            "from backend.auth import register_user, login_user\n"
            "ok, uid = register_user('E2E User', 'e2e@test.com', '9999999999', 'Passw0rd!')\n"
            "assert ok, uid\n"
            "ok2, user = login_user('e2e@test.com', 'Passw0rd!')\n"
            "assert ok2 and user['role'] == 'citizen'\n"
            "print('E2E-OK')\n"
            "os.remove(tmpdb)\n"
        )
        result = self._run(code, env_extra={})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("E2E-OK", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
