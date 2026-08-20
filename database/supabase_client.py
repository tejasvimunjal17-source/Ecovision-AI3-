"""
database/supabase_client.py
------------------------------
STEP 5A -- PREPARATION LAYER ONLY. This module is NOT imported or
called anywhere else in the application yet. database/db.py's sqlite3
implementation remains the app's one and only active data backend.

What this file is for: a single, reusable place to obtain a configured
Supabase client, once a future step actually starts reading/writing to
Supabase. Building it now (rather than later, inline, ad-hoc) means the
eventual cutover only has to change database/db.py's internals, not
invent client setup from scratch under time pressure.

Planned usage (NOT implemented yet -- see the module's own comments in
config/settings.py::USE_SUPABASE):
    from database.supabase_client import get_client
    client = get_client()  # anon key -- respects RLS
    client.table("notifications").select("*").eq("id", 1).execute()

    from database.supabase_client import get_admin_client
    admin = get_admin_client()  # service-role key -- BYPASSES RLS
    # only for trusted server-side/admin operations, e.g. one-time seeding

Credentials: never hardcoded. Always read through config/settings.py's
_get() precedence chain (st.secrets -> .env/environment -> default),
exactly like every other integration in this codebase (OpenRouter,
Google OAuth, Adzuna, Jooble).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from config import settings

logger = logging.getLogger("ecovision.supabase")

try:
    from supabase import create_client, Client  # type: ignore
    _SUPABASE_SDK_AVAILABLE = True
except ImportError:
    # supabase-py not installed, or installed but import failed for some
    # other reason -- both are non-fatal here since nothing calls this
    # module yet. is_client_available() below reports this cleanly
    # instead of letting an ImportError surface deep in unrelated code.
    Client = None  # type: ignore
    _SUPABASE_SDK_AVAILABLE = False


def is_sdk_available() -> bool:
    """True once the `supabase` PyPI package is importable."""
    return _SUPABASE_SDK_AVAILABLE


@lru_cache(maxsize=1)
def get_client() -> "Client | None":
    """
    Anon-key client -- subject to Row Level Security (the policies
    already written in database/migrations/010_indexes_and_rls.sql, not
    yet activated against any real data). This is the client a future
    step should use for ordinary user-scoped reads/writes once the
    Supabase Auth <-> auth_user_id bridge (see the Step 5 audit, section
    E) is actually built.

    Returns None (never raises) if the SDK isn't installed or
    credentials aren't configured -- callers must check for None, same
    pattern as this codebase's other optional integrations
    (config.settings.is_ai_configured(), etc.).
    """
    if not _SUPABASE_SDK_AVAILABLE:
        logger.info("Supabase client not created: `supabase` package is not installed.")
        return None
    if not settings.is_supabase_configured():
        logger.info("Supabase client not created: SUPABASE_URL / SUPABASE_ANON_KEY not configured.")
        return None
    try:
        return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    except Exception:
        logger.exception("Failed to create Supabase client (anon key)")
        return None


@lru_cache(maxsize=1)
def get_admin_client() -> "Client | None":
    """
    Service-role client -- BYPASSES Row Level Security entirely. Same
    role the app's SQLite layer effectively plays today (no per-request
    RLS at all, only the app-layer WHERE-clause scoping already in
    backend/*.py). Intended for trusted server-side operations only:
    migrations, one-time seeding, admin tooling -- never for handling
    an individual user's request directly. Returns None if not
    configured, same contract as get_client().
    """
    if not _SUPABASE_SDK_AVAILABLE:
        logger.info("Supabase admin client not created: `supabase` package is not installed.")
        return None
    if not settings.is_supabase_admin_configured():
        logger.info("Supabase admin client not created: SUPABASE_SERVICE_ROLE_KEY not configured.")
        return None
    try:
        return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        logger.exception("Failed to create Supabase client (service role key)")
        return None


def health_check() -> dict:
    """
    Lightweight, side-effect-free connectivity/configuration check.
    Does NOT touch any application table (deliberately -- Step 5A must
    not assume any Supabase table/data exists yet). Safe to call even
    when nothing is configured; never raises.

    Returns a dict describing exactly what is and isn't ready, e.g.:
        {"sdk_installed": True, "configured": False, "admin_configured": False,
         "reachable": False, "detail": "SUPABASE_URL / SUPABASE_ANON_KEY not configured"}
    """
    result = {
        "sdk_installed": _SUPABASE_SDK_AVAILABLE,
        "configured": settings.is_supabase_configured(),
        "admin_configured": settings.is_supabase_admin_configured(),
        "reachable": False,
        "detail": "",
    }

    if not _SUPABASE_SDK_AVAILABLE:
        result["detail"] = "The `supabase` package is not installed (see requirements.txt)."
        return result

    if not result["configured"]:
        result["detail"] = "SUPABASE_URL / SUPABASE_ANON_KEY are not set (or still placeholder values)."
        return result

    client = get_client()
    if client is None:
        result["detail"] = "Client could not be created -- check credentials."
        return result

    # A minimal, harmless call: ask Supabase Auth for the current
    # (anonymous) session config. This confirms the URL/key pair can
    # actually reach the project without depending on any table --
    # important since no Supabase table has real data yet in Step 5A.
    try:
        client.auth.get_session()
        result["reachable"] = True
        result["detail"] = "Reached the configured Supabase project."
    except Exception as e:
        result["detail"] = f"Client created, but connectivity check failed: {e}"

    return result
