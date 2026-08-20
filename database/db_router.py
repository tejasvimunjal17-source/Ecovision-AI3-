"""
database/db_router.py
------------------------
STEP 5C -- the routing layer described in the Step 5B/5C plan:

    USE_SUPABASE=false -> database/db.py     (SQLite, unchanged)
    USE_SUPABASE=true  -> database/db_supabase.py  (Postgres adapter)

NOT YET ADOPTED: no file in backend/, chatbot/, pages/, or app.py
imports from this module yet -- they all still `from database.db
import execute, fetch_one, fetch_all` directly, unchanged, so today's
application behavior is completely untouched by this file's existence.
Adopting it (switching those imports to `from database.db_router import
...`) is Step 5D's job, not this one.

NO SILENT FALLBACK (explicit requirement for this step): when
USE_SUPABASE=true, a configuration or connection problem in
database/db_supabase.py is allowed to raise straight through this
router -- it is never caught here and rerouted to SQLite. Hiding a
Supabase misconfiguration behind a working SQLite fallback would be
exactly the kind of silent failure that makes a real migration
dangerous to debug later.
"""
from config import settings

if settings.USE_SUPABASE:
    from database.db_supabase import execute, fetch_one, fetch_all, get_connection, init_db  # noqa: F401
    _ACTIVE_BACKEND = "supabase"
else:
    from database.db import execute, fetch_one, fetch_all, get_connection, init_db  # noqa: F401
    _ACTIVE_BACKEND = "sqlite"


def active_backend() -> str:
    """Returns "sqlite" or "supabase" -- which implementation this router resolved to at import time."""
    return _ACTIVE_BACKEND
