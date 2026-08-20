"""
config/settings.py
--------------------
Loads all configuration in one place so the rest of the app never
touches os.environ / st.secrets directly.

CREDENTIAL PRECEDENCE (this is the actual fix for "I uploaded .env but
it's not picking up my key" on Streamlit Community Cloud):
    1. st.secrets  (Streamlit Community Cloud "Secrets" — the real,
       production credentials once deployed)
    2. .env / process environment (local development only)
    3. a documented default (for non-secret settings only — never for
       API keys)

On Streamlit Community Cloud, a local .env file is NOT read by the
platform at all — only whatever you paste into the app's "Secrets" box
(Settings -> Secrets) is available at runtime, via st.secrets. A repo
containing only a placeholder .env / .env.example (as recommended —
never commit real keys) will therefore always look "unconfigured" on
Cloud unless the same keys are also set in Secrets. This module reads
st.secrets first specifically so that once you add your real key there,
it's picked up automatically with zero code changes.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Known placeholder values from .env.example — used to distinguish "key is
# genuinely unset/still a template" from "key is set but happens to be
# short/odd", so the app can show an accurate, specific status message
# instead of silently pretending a placeholder is a real credential.
_PLACEHOLDER_MARKERS = (
    "your_openrouter_api_key_here",
    "your_ibm_cloud_api_key_here",
    "your_watsonx_project_id_here",
    "sk-or-v1-xxxxxxxxxxxxxxxx",
    "your_supabase_project_url_here",
    "your_supabase_anon_key_here",
    "your_supabase_service_role_key_here",
    "your_supabase_db_url_here",
    "https://<your-project-ref>.supabase.co",
    "postgresql://postgres:[your-password]@db.<your-project-ref>.supabase.co:5432/postgres",
    "change_this",
    "",
)


def _get(name: str, default: str = "") -> str:
    """
    Read a config value with the precedence documented above:
    st.secrets -> .env/environment -> default.
    Safe to call even when no secrets.toml exists at all (st.secrets
    raises/behaves oddly with zero configured secrets in some Streamlit
    versions -- guarded here so that case never crashes the app).
    """
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            try:
                if name in st.secrets:
                    return str(st.secrets[name]).strip()
            except Exception:
                pass  # no secrets.toml locally / secrets not configured yet -- fall through to .env
    except Exception:
        pass  # streamlit not importable in this context (e.g. a plain script) -- fall through to .env

    return os.getenv(name, default).strip()


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return v in _PLACEHOLDER_MARKERS or v.startswith("your_") or "xxxxxxxx" in v


# ---------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = _get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# TWO separate models, as required: one for the Prakriti AI Connect
# chatbot (text-only is fine and cheaper/faster), one for waste-image
# classification (MUST be vision-capable). Reading the *_CHAT_MODEL /
# *_VISION_MODEL names first, but still honoring the older
# OPENROUTER_TEXT_MODEL / OPENROUTER_MODEL names if that's what's in an
# existing .env or Secrets block, so nobody's existing configuration
# silently breaks.
OPENROUTER_CHAT_MODEL = (
    _get("OPENROUTER_CHAT_MODEL")
    or _get("OPENROUTER_TEXT_MODEL")
    or "meta-llama/llama-3.3-70b-instruct:free"
)

# Default vision model: OpenRouter's own auto-router ("openrouter/free"),
# which selects a currently-live free model that supports image
# understanding for each request. This is a deliberate choice over
# pinning one specific free vision model slug: individual free vision
# models on OpenRouter rotate/get retired over time (this is exactly
# what happened to the previous default, meta-llama/llama-3.2-11b-
# vision-instruct:free, which is no longer available and was the cause
# of the reported 404 -- OpenRouter returns 404 for an unknown/retired
# model slug). "openrouter/free" is designed specifically to stay
# working across that churn. Set OPENROUTER_VISION_MODEL to pin a
# specific model instead if you prefer (e.g. "qwen/qwen2.5-vl-32b-
# instruct:free" or "google/gemma-3-27b-it:free" are known-good
# vision-capable free options as of this writing).
OPENROUTER_VISION_MODEL = (
    _get("OPENROUTER_VISION_MODEL")
    or _get("OPENROUTER_MODEL")
    or "openrouter/free"
)

OPENROUTER_SITE_URL = _get("OPENROUTER_SITE_URL", "https://ecovision-ai.streamlit.app")
OPENROUTER_APP_NAME = _get("OPENROUTER_APP_NAME", "EcoVision AI")

# ---------------------------------------------------------------------
# IBM watsonx.ai (credential plumbing — available for any feature that
# wants to call watsonx.ai; OpenRouter remains the primary AI provider
# wired through the rest of the app today)
# ---------------------------------------------------------------------
WATSONX_API_KEY = _get("WATSONX_API_KEY")
WATSONX_PROJECT_ID = _get("WATSONX_PROJECT_ID")
WATSONX_URL = _get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
WATSONX_MODEL_ID = _get("WATSONX_MODEL_ID", "ibm/granite-3-8b-instruct")

# ---- Security ----
APP_SECRET_KEY = _get("APP_SECRET_KEY", "dev-secret-change-me")
SESSION_TIMEOUT_MINUTES = int(_get("SESSION_TIMEOUT_MINUTES", "60") or 60)

# ---- Live Job Search APIs (optional — modules degrade gracefully if unset) ----
ADZUNA_APP_ID = _get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = _get("ADZUNA_APP_KEY")
ADZUNA_COUNTRY = _get("ADZUNA_COUNTRY", "in")

JOOBLE_API_KEY = _get("JOOBLE_API_KEY")

# ---- Database ----
DATABASE_PATH = str(BASE_DIR / _get("DATABASE_PATH", "database/ecovision.db"))

# ---- Supabase (Step 5A: preparation layer only) ----
# USE_SUPABASE is the cutover switch for a FUTURE step -- it deliberately
# defaults to "false" so today's SQLite-backed application keeps working
# exactly as before, with zero behavior change, regardless of whether
# any Supabase credentials below happen to be configured. Nothing in
# database/db.py reads this flag yet (see database/supabase_client.py's
# module docstring for the planned Step 5B/5C wiring).
USE_SUPABASE = _get("USE_SUPABASE", "false").strip().lower() in ("1", "true", "yes", "on")

SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_ANON_KEY = _get("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = _get("SUPABASE_SERVICE_ROLE_KEY")

# Direct Postgres connection string (Supabase Dashboard -> Project
# Settings -> Database -> Connection string). This is DIFFERENT from
# SUPABASE_URL above (that's the REST/Auth/Storage API base URL, used
# by database/supabase_client.py's supabase-py client). SUPABASE_DB_URL
# is used only by database/db_supabase.py (Step 5C), which talks to
# Postgres directly via psycopg -- needed because this codebase's
# execute()/fetch_one()/fetch_all() API expects arbitrary raw
# parameterized SQL, which PostgREST (what supabase-py wraps) does not
# support executing directly.
SUPABASE_DB_URL = _get("SUPABASE_DB_URL")

# ---- Branding ----
MUNICIPALITY_NAME = _get("MUNICIPALITY_NAME", "Municipal Corporation of Gurugram (MCG)")
SUPPORT_EMAIL = _get("SUPPORT_EMAIL", "support@ecovision-ai.example.in")
SUPPORT_PHONE = _get("SUPPORT_PHONE", "+91-9999999999")

APP_NAME = "EcoVision AI"
APP_TAGLINE = "AI-powered Smart City Platform for Sustainable Waste Management"

WASTE_CATEGORIES = [
    "Plastic", "Organic", "Paper", "Glass", "Metal",
    "Mixed", "E-Waste", "Biomedical", "Construction",
]

PRIORITY_LEVELS = ["Low", "Medium", "High"]

COMPLAINT_STATUSES = ["Submitted", "Under Review", "Assigned", "In Progress", "Resolved", "Rejected"]

REWARD_POINTS = {
    "complaint_submitted": 10,
    "complaint_resolved_bonus": 20,
    "correct_segregation": 15,
    "referral": 25,
}

# ---- Accepted media types for waste reporting ----
# Extensions WITHOUT the leading dot, for use with st.file_uploader(type=...).
ACCEPTED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
ACCEPTED_VIDEO_EXTENSIONS = ["mp4", "mov", "avi", "mkv", "webm"]
# MIME types actually used when building the OpenRouter vision request.
IMAGE_MIME_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "webp": "image/webp",
}


def is_ai_configured() -> bool:
    """True once a real (non-placeholder) OpenRouter key is present -- used for chat/text features."""
    return bool(OPENROUTER_API_KEY) and not _is_placeholder(OPENROUTER_API_KEY)


def is_vision_configured() -> bool:
    """
    True once a real OpenRouter key AND a vision model are present.
    Separate from is_ai_configured() so the Report Waste page can show a
    vision-specific status message distinct from the general chatbot one.
    """
    return is_ai_configured() and bool(OPENROUTER_VISION_MODEL) and not _is_placeholder(OPENROUTER_VISION_MODEL)


def is_watsonx_configured() -> bool:
    return (
        bool(WATSONX_API_KEY) and not _is_placeholder(WATSONX_API_KEY)
        and bool(WATSONX_PROJECT_ID) and not _is_placeholder(WATSONX_PROJECT_ID)
    )


def ai_not_configured_reason() -> str:
    """
    A specific, human-readable reason why AI features are unavailable --
    used instead of a generic "not configured" so users (and you, while
    debugging) know exactly what to fix. Only relevant once BOTH
    OpenRouter and watsonx.ai are unconfigured (either one alone is
    sufficient for vision classification — see is_vision_configured() /
    is_watsonx_configured() and classify_waste_image()'s fallback logic).
    """
    if is_watsonx_configured():
        return "IBM watsonx.ai is configured."
    if not OPENROUTER_API_KEY:
        return "OPENROUTER_API_KEY is not set, and no IBM watsonx.ai credentials are configured either."
    if _is_placeholder(OPENROUTER_API_KEY):
        return ("OPENROUTER_API_KEY is still the placeholder value from .env.example, and no IBM "
                "watsonx.ai credentials are configured either — set at least one to enable AI features.")
    return "OpenRouter is configured."


def is_google_oauth_configured() -> bool:
    """
    True once Streamlit's native auth (st.login()) has a real, non-placeholder
    [auth] section in secrets.toml -- see .streamlit/secrets.toml.example and
    DEPLOYMENT.md's "Google OAuth Setup" section. Checked directly against
    st.secrets (not mirrored into settings.* constants above) because
    Streamlit's auth system reads that same [auth] section itself; this
    function only tells the UI whether to treat the button as live or as
    "not configured yet" -- it never reads or exposes the actual secret
    values.
    """
    try:
        import streamlit as st
        if not hasattr(st, "secrets") or "auth" not in st.secrets:
            return False
        auth = st.secrets["auth"]
        required = ("client_id", "client_secret", "redirect_uri", "cookie_secret", "server_metadata_url")
        return all(not _is_placeholder(str(auth.get(k, ""))) for k in required)
    except Exception:
        return False


def is_adzuna_configured() -> bool:
    return bool(ADZUNA_APP_ID) and bool(ADZUNA_APP_KEY)


def is_jooble_configured() -> bool:
    return bool(JOOBLE_API_KEY)


def is_supabase_configured() -> bool:
    """
    True once real (non-placeholder) SUPABASE_URL + SUPABASE_ANON_KEY are
    present. Only tells you credentials exist -- NOT that the app is
    using Supabase (that's USE_SUPABASE, which stays False regardless of
    this function's result until a future step). Service-role key is
    checked separately by is_supabase_admin_configured(), since not
    every use of the client needs it.
    """
    return (
        bool(SUPABASE_URL) and not _is_placeholder(SUPABASE_URL)
        and bool(SUPABASE_ANON_KEY) and not _is_placeholder(SUPABASE_ANON_KEY)
    )


def is_supabase_admin_configured() -> bool:
    """True once a real (non-placeholder) service-role key is also present."""
    return is_supabase_configured() and bool(SUPABASE_SERVICE_ROLE_KEY) and not _is_placeholder(SUPABASE_SERVICE_ROLE_KEY)


def is_supabase_db_configured() -> bool:
    """
    True once a real (non-placeholder) direct Postgres connection string
    is present. Separate from is_supabase_configured() (the REST/Auth
    API credentials) -- database/db_supabase.py needs THIS one.
    """
    return bool(SUPABASE_DB_URL) and not _is_placeholder(SUPABASE_DB_URL)
