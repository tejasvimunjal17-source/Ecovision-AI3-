"""
utils/certification_search.py
--------------------------------
Module 1: Certification Finder — fully independent live-search module.

ARCHITECTURE (updated)
------------------------
This module has NO dependency on any local certification dataset. It
combines results from THREE tiers, run independently so a failure in one
never affects another:

TIER 1 — LIVE OFFICIAL APIs (network calls, cached, rate-limited)
    - Microsoft Learn Catalog API (learn.microsoft.com/api/catalog/) —
      verified official, public, unauthenticated. This is now the
      PRIMARY live source (see _fetch_microsoft_learn).
    - Coursera Courses catalog API (api.coursera.org/api/courses.v1) —
      kept as an OPTIONAL, best-effort source. It is explicitly NOT
      relied on as the only live provider anymore: if it's unreachable
      (which is common — see the "no results" investigation from the
      previous debugging pass), the page still returns full live results
      from Microsoft Learn, plus every Tier 2 provider below, with zero
      degradation (see _fetch_coursera).

TIER 2 — OFFICIAL SEARCH ENDPOINTS (zero network calls, always available)
    For every provider without a public search API (IBM SkillsBuild,
    Microsoft Learn's own search page, Google Cloud Skills Boost, Cisco
    Skills For All, Oracle University, AWS Skill Builder, NPTEL, SWAYAM,
    edX, FutureLearn, LinkedIn Learning, Kaggle Learn, Hugging Face,
    FreeCodeCamp, GitHub Skills, and now Red Hat Training + Skill India
    Digital), we link straight to *that provider's own* documented
    search/catalog URL with the query pre-filled. This is navigation,
    never scraping — no third-party content is copied or displayed as
    if it were ours. See OFFICIAL_SEARCH_PROVIDERS.

TIER 3 — AI-GENERATED RECOMMENDATION (fallback when live results are thin)
    Unchanged from before — see _ai_recommend_similar.

WHY MICROSOFT LEARN AND NOT THE OTHER 6 REQUESTED PROVIDERS AS LIVE APIs
----------------------------------------------------------------------------
Before writing any code, each of the following was checked against its
own official developer documentation: Microsoft Learn, Cisco Skills For
All, Google Cloud Skills Boost, AWS Skill Builder, IBM SkillsBuild,
Oracle University, Red Hat Training. Only Microsoft Learn publishes a
genuine public, unauthenticated, documented catalog API. The other six
do not expose one (confirmed by their own docs/dev portals, not assumed)
— for those, Tier 2's official-search-URL approach is the CORRECT
behavior per the required priority ladder (Official API -> official
RSS/JSON feed -> official search URL), not a shortfall. If any of them
publish a public API in the future, adding it is a matter of writing one
more _fetch_<provider>() function and one more _run_live_provider(...)
call in search_certifications() — the architecture below was built
specifically to make that a small, additive change.

RELIABILITY DESIGN (this is the actual "fix" for the Coursera problem)
----------------------------------------------------------------------------
1. Every live provider goes through _run_live_provider(), which:
     - Applies the existing (unchanged) per-provider rate limit.
     - Checks a short (1-2 minute) failure cooldown BEFORE attempting a
       network call, so a provider that just failed isn't hammered again
       on every rerun — but recovers quickly, unlike a stale cached
       "0 results" that would otherwise sit for the full success-cache
       TTL. This is the "cache failures for only 1-2 minutes" requirement.
     - Catches every failure mode (HTTP error incl. 401/403, connection/
       timeout errors, malformed JSON, and — as a last-resort guard —
       any other unexpected exception) and turns it into one of the
       required provider_status labels. NOTHING escapes this function;
       one provider failing can never break the page or another provider.
     - On genuine success, merges results into the shared live_results
       list — successful fetches are still cached via st.cache_data at
       the ORIGINAL, unchanged CACHE_TTL_SECONDS (10 minutes). Only the
       failure path uses the new short cooldown.
2. Each _fetch_<provider>() function now RAISES on failure instead of
   swallowing the exception and returning []. This is what makes (1)
   possible, and it also fixes a subtle pre-existing bug: because
   st.cache_data never caches a raised exception, a transient failure is
   retried on the next eligible attempt instead of being remembered as
   "confirmed 0 results" for the full 10-minute cache TTL.

Everything else — the public search_certifications() signature and
4-key return shape, _apply_filters, the OFFICIAL_SEARCH_PROVIDERS list,
_official_search_cards, _ai_recommend_similar, and the overall design
goal of "never return an empty page" — is unchanged from before.
"""
import time
import logging
import difflib
import requests
import streamlit as st
from urllib.parse import quote

from utils.ai_client import chat_completion

logger = logging.getLogger("ecovision.certsearch")

REQUEST_TIMEOUT = 8
CACHE_TTL_SECONDS = 600          # unchanged — successful results, as before
FAILURE_COOLDOWN_SECONDS = 90    # NEW — 1.5 min short cooldown for failed providers (per architecture requirement: "cache failures for only 1-2 minutes")
MIN_SECONDS_BETWEEN_LIVE_CALLS = 3  # unchanged — rate limiting

# ---------------------------------------------------------------------
# TIER 2: Providers without a public search API — link straight to their
# own official search/catalog page using each site's documented URL
# pattern. {q} is replaced with the URL-encoded search query.
#
# UNCHANGED: none of the original 15 entries below were modified or
# removed. ADDED: Red Hat Training and Skill India Digital (new,
# appended at the end) per the requested provider list + "another
# official providers in India and abroad".
# ---------------------------------------------------------------------
OFFICIAL_SEARCH_PROVIDERS = [
    {"name": "IBM SkillsBuild", "url_tpl": "https://skillsbuild.org/catalog-search?q={q}",
     "skills": ["ai", "cloud", "data", "cybersecurity", "sustainability"]},
    {"name": "Microsoft Learn", "url_tpl": "https://learn.microsoft.com/en-us/search/?terms={q}",
     "skills": ["azure", "cloud", "data", "ai", "power platform"]},
    {"name": "Google Cloud Skills Boost", "url_tpl": "https://www.cloudskillsboost.google/catalog?keywords={q}",
     "skills": ["cloud", "gcp", "data", "ai", "kubernetes"]},
    {"name": "Cisco Skills For All", "url_tpl": "https://skillsforall.com/search?q={q}",
     "skills": ["networking", "cybersecurity", "iot"]},
    {"name": "Oracle University", "url_tpl": "https://mylearn.oracle.com/ou/search?q={q}",
     "skills": ["cloud", "database", "java"]},
    {"name": "AWS Skill Builder", "url_tpl": "https://skillbuilder.aws/search?searchText={q}",
     "skills": ["cloud", "aws", "ai", "data"]},
    {"name": "NPTEL", "url_tpl": "https://nptel.ac.in/courses?search={q}",
     "skills": ["engineering", "sustainability", "environment", "science"]},
    {"name": "SWAYAM", "url_tpl": "https://swayam.gov.in/explorer?searchText={q}",
     "skills": ["sustainability", "environment", "management", "humanities"]},
    {"name": "edX", "url_tpl": "https://www.edx.org/search?q={q}",
     "skills": ["climate", "data", "business", "computer science"]},
    {"name": "FutureLearn", "url_tpl": "https://www.futurelearn.com/search?q={q}",
     "skills": ["sustainability", "health", "business"]},
    {"name": "LinkedIn Learning", "url_tpl": "https://www.linkedin.com/learning/search?keywords={q}",
     "skills": ["business", "leadership", "sustainability", "software"]},
    {"name": "Kaggle Learn", "url_tpl": "https://www.kaggle.com/search?q={q}",
     "skills": ["machine learning", "data", "python"]},
    {"name": "Hugging Face Courses", "url_tpl": "https://huggingface.co/learn",
     "skills": ["nlp", "machine learning", "ai"]},
    {"name": "FreeCodeCamp", "url_tpl": "https://www.freecodecamp.org/news/search/?query={q}",
     "skills": ["web development", "programming", "python", "javascript"]},
    {"name": "GitHub Skills", "url_tpl": "https://github.com/skills",
     "skills": ["git", "programming", "open source"]},
    # --- NEW additions below (nothing above this line was changed) ---
    {"name": "Red Hat Training", "url_tpl": "https://www.redhat.com/en/services/training/all-courses-exams?search={q}",
     "skills": ["linux", "cloud", "kubernetes", "devops", "automation", "openshift"]},
    {"name": "Skill India Digital", "url_tpl": "https://www.skillindiadigital.gov.in/search?query={q}",
     "skills": ["vocational", "government", "india", "employability", "trades"]},
]

# ---------------------------------------------------------------------
# Provider status vocabulary (NEW — replaces the old ambiguous
# "ok" / "no_results_or_unavailable" / "rate_limited (...)" strings with
# the exact required vocabulary, applied uniformly by _run_live_provider).
# ---------------------------------------------------------------------
STATUS_API_OK = "API OK"
STATUS_NO_MATCHES = "No matching courses"
STATUS_UNAVAILABLE = "Provider unavailable"
STATUS_SEARCH_LINK = "Using official search link"
STATUS_RATE_LIMITED = "Rate limited"
STATUS_AUTH_REQUIRED = "Authentication required"


def _rate_limit_ok(key: str) -> bool:
    """UNCHANGED — same rate-limiting mechanism/behavior as before."""
    last = st.session_state.get(f"_cert_rl_{key}", 0)
    now = time.time()
    if now - last < MIN_SECONDS_BETWEEN_LIVE_CALLS:
        return False
    st.session_state[f"_cert_rl_{key}"] = now
    return True


# ---------------------------------------------------------------------
# NEW: short (1-2 min) failure cooldown, separate from the long-lived
# st.cache_data success cache. Session-scoped, mirroring the existing
# _rate_limit_ok pattern above rather than fighting st.cache_data's
# pure-function caching model (which has no clean way to cache "this
# failed" for a shorter TTL than "this succeeded").
# ---------------------------------------------------------------------
def _provider_in_cooldown(key: str) -> bool:
    failed_at = st.session_state.get(f"_cert_fail_{key}", 0)
    return (time.time() - failed_at) < FAILURE_COOLDOWN_SECONDS


def _mark_provider_failed(key: str) -> None:
    st.session_state[f"_cert_fail_{key}"] = time.time()


# ---------------------------------------------------------------------
# TIER 1, PROVIDER A: Microsoft Learn Catalog API (NEW)
# Verified official & public: https://learn.microsoft.com/en-us/training/support/catalog-api
# No authentication required. Returns the full public catalog (modules,
# certifications, etc.) as one JSON payload — there's no server-side
# search parameter, so we filter client-side per query below.
# Documented as functioning at least through mid-2026 before Microsoft's
# newer, authenticated "Learn Platform API" supersedes it; if/when that
# happens, only _fetch_ms_learn_catalog() below needs to change.
# ---------------------------------------------------------------------
MS_LEARN_CATALOG_TTL_SECONDS = 3600  # catalog updates at most ~daily per MS docs — cache longer than a single search


@st.cache_data(ttl=MS_LEARN_CATALOG_TTL_SECONDS, show_spinner=False)
def _fetch_ms_learn_catalog():
    """
    Raw cached catalog fetch. Raises on failure (not caught here) — see
    module docstring: st.cache_data only caches successful returns, so a
    transient failure is retried on the next attempt rather than being
    remembered as empty for a full hour.
    """
    resp = requests.get(
        "https://learn.microsoft.com/api/catalog/",
        params={"type": "modules,certifications", "locale": "en-us"},
        headers={"User-Agent": "EcoVisionAI/1.0 (+https://ecovision-ai.streamlit.app)"},
        timeout=REQUEST_TIMEOUT * 2,  # this payload can be several MB
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_microsoft_learn(query: str, limit: int = 12):
    """
    Shapes the cached raw catalog into this module's common result dict
    (same shape Coursera already produces, so _apply_filters/_rank/the
    page's _render_result_card all work unchanged). Raises on failure —
    handled centrally by _run_live_provider.
    """
    catalog = _fetch_ms_learn_catalog()
    q = (query or "").lower().strip()
    results = []

    for m in catalog.get("modules", []):
        title = m.get("title", "") or ""
        summary = m.get("summary", "") or ""
        if q and q not in title.lower() and q not in summary.lower():
            continue
        duration = m.get("duration_in_minutes") or m.get("duration")
        levels = m.get("levels") or []
        results.append({
            "title": title or "Untitled Module",
            "provider": "Microsoft Learn",
            "description": (summary or "No description provided.")[:260],
            "level": str(levels[0]).title() if levels else "-",
            "duration": f"{duration} min" if isinstance(duration, (int, float)) else "-",
            "language": "English",
            "certificate": False,   # modules themselves aren't certifications
            "free": True,           # MS Learn training content is free to take
            "url": m.get("url") or "https://learn.microsoft.com/training/",
            "logo": m.get("iconUrl") or "",
            "last_updated": (m.get("lastModified") or "")[:10] or None,
            "source": "Microsoft Learn Official API",
        })
        if len(results) >= limit:
            return results

    for c in catalog.get("certifications", []):
        title = c.get("title", "") or ""
        summary = c.get("summary", "") or ""
        if q and q not in title.lower() and q not in summary.lower():
            continue
        levels = c.get("levels") or []
        results.append({
            "title": title or "Untitled Certification",
            "provider": "Microsoft Learn",
            "description": (summary or "No description provided.")[:260],
            "level": str(levels[0]).title() if levels else "-",
            "duration": "-",
            "language": "English",
            "certificate": True,
            "free": None,  # training is free; the certification EXAM typically has a fee
            "url": c.get("url") or "https://learn.microsoft.com/credentials/",
            "logo": c.get("iconUrl") or "",
            "last_updated": (c.get("lastModified") or "")[:10] or None,
            "source": "Microsoft Learn Official API",
        })
        if len(results) >= limit:
            break

    return results


# ---------------------------------------------------------------------
# TIER 1, PROVIDER B: Coursera Courses catalog API (UNCHANGED shaping
# logic — only the error handling moved out, see module docstring).
# Now explicitly OPTIONAL: search_certifications() no longer depends on
# this being reachable for the page to return live results.
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_coursera(query: str, limit: int = 12):
    """
    Official, best-effort Coursera Courses catalog API.
    https://api.coursera.org/api/courses.v1
    Raises on failure — see module docstring for why (centralized error
    handling + short failure cooldown in _run_live_provider).
    """
    resp = requests.get(
        "https://api.coursera.org/api/courses.v1",
        params={
            "q": "search",
            "query": query,
            "limit": limit,
            "fields": "name,description,photoUrl,slug,workload",
        },
        headers={"User-Agent": "EcoVisionAI/1.0 (+https://ecovision-ai.streamlit.app)"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for c in data.get("elements", []):
        slug = c.get("slug")
        results.append({
            "title": c.get("name") or "Untitled Course",
            "provider": "Coursera",
            "description": (c.get("description") or "No description provided.")[:260],
            "level": "-",
            "duration": c.get("workload") or "-",
            "language": "-",
            "certificate": True,
            "free": None,  # audit-free availability varies per course; not exposed by this endpoint
            "url": f"https://www.coursera.org/learn/{slug}" if slug else "https://www.coursera.org/",
            "logo": c.get("photoUrl") or "",
            "last_updated": None,
            "source": "Coursera Official API",
        })
    return results


# ---------------------------------------------------------------------
# NEW: uniform runner for every Tier-1 (live API) provider. This is the
# core reliability fix — adding it is what makes Coursera "optional"
# instead of load-bearing, and makes adding future providers trivial.
# ---------------------------------------------------------------------
def _run_live_provider(name, rate_limit_key, fetch_fn, query, provider_status, live_results):
    """
    Runs one live provider fetch with full isolation:
      - unchanged rate limiting (_rate_limit_ok)
      - NEW short failure cooldown (skips the network call entirely if
        this provider failed in the last FAILURE_COOLDOWN_SECONDS)
      - classifies every outcome into the required provider_status
        vocabulary
      - can NEVER raise — a bug or unexpected response shape in one
        provider's fetch function is caught here and degrades to a
        status string, so the page and every other provider keep working
    Mutates `provider_status` (dict) and `live_results` (list) in place.
    """
    if not _rate_limit_ok(rate_limit_key):
        provider_status[name] = STATUS_RATE_LIMITED
        return

    if _provider_in_cooldown(rate_limit_key):
        provider_status[name] = STATUS_UNAVAILABLE
        return

    try:
        results = fetch_fn(query)
    except requests.exceptions.HTTPError as e:
        status_code = getattr(e.response, "status_code", None)
        _mark_provider_failed(rate_limit_key)
        provider_status[name] = STATUS_AUTH_REQUIRED if status_code in (401, 403) else STATUS_UNAVAILABLE
        logger.warning("%s provider failed (HTTP %s): %s", name, status_code, e)
        return
    except requests.exceptions.RequestException as e:
        _mark_provider_failed(rate_limit_key)
        provider_status[name] = STATUS_UNAVAILABLE
        logger.warning("%s provider unreachable: %s", name, e)
        return
    except (ValueError, KeyError) as e:
        _mark_provider_failed(rate_limit_key)
        provider_status[name] = STATUS_UNAVAILABLE
        logger.warning("%s provider returned unexpected data: %s", name, e)
        return
    except Exception as e:  # last-resort guard — see docstring
        _mark_provider_failed(rate_limit_key)
        provider_status[name] = STATUS_UNAVAILABLE
        logger.error("%s provider raised an unexpected error: %s", name, e)
        return

    live_results.extend(results)
    provider_status[name] = STATUS_API_OK if results else STATUS_NO_MATCHES


def _dedupe_results(results):
    """NEW: removes duplicate live results (same title+provider) before ranking, in case two providers ever surface an overlapping item."""
    seen = set()
    deduped = []
    for r in results:
        key = (str(r.get("title", "")).strip().lower(), str(r.get("provider", "")).strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _official_search_cards(query: str):
    """UNCHANGED — zero-network fallback: direct links to each provider's own official search page."""
    q_lower = query.lower()
    scored = []
    for p in OFFICIAL_SEARCH_PROVIDERS:
        relevance = 1 if any(s in q_lower or q_lower in s for s in p["skills"]) else 0
        scored.append((relevance, p))
    scored.sort(key=lambda t: t[0], reverse=True)

    cards = []
    for relevance, p in scored:
        url = p["url_tpl"].format(q=quote(query or "sustainability"))
        cards.append({
            "title": f"Search '{query or 'certifications'}' on {p['name']}",
            "provider": p["name"],
            "description": "Official provider search page — live catalog results open directly on their site.",
            "level": "-", "duration": "-", "language": "-", "certificate": None, "free": None,
            "url": url, "logo": "", "last_updated": None,
            "source": "Official Search Endpoint",
            "_relevance": relevance,
        })
    return cards


def _apply_filters(results, level, duration_kw, language, certificate, free):
    """UNCHANGED."""
    def keep(r):
        if level != "Any" and r.get("level") not in ("-", level):
            return False
        if duration_kw and duration_kw.lower() not in str(r.get("duration", "")).lower():
            return False
        if language != "Any" and r.get("language") not in ("-", language):
            return False
        if certificate == "Yes" and r.get("certificate") is False:
            return False
        if certificate == "No" and r.get("certificate") is True:
            return False
        if free == "Free" and r.get("free") is False:
            return False
        if free == "Paid" and r.get("free") is True:
            return False
        return True
    return [r for r in results if keep(r)]


def _rank(results, query):
    """
    Same signature/contract as before (results, query) -> sorted results.
    Extended with small tie-break bonuses per the architecture
    requirements (free / beginner-friendly / certificate-bearing courses
    rank slightly higher). Text relevance to the query remains the
    dominant factor, exactly as before — these are tie-breakers, not a
    replacement for relevance ranking.
    """
    def score(r):
        text = f"{r['title']} {r.get('description','')} {r['provider']}".lower()
        relevance = 1.0 if query.lower() in text else difflib.SequenceMatcher(None, query.lower(), text).ratio()
        bonus = 0.0
        if r.get("free") is True:
            bonus += 0.03
        if str(r.get("level", "")).lower() == "beginner":
            bonus += 0.02
        if r.get("certificate") is True:
            bonus += 0.02
        return relevance + bonus
    return sorted(results, key=score, reverse=True)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _ai_recommend_similar(query: str):
    """UNCHANGED — AI-generated 'similar certifications' suggestion when live results are thin."""
    prompt = (
        f"A learner searched for a free certification on '{query}' but we found few or no direct catalog "
        "matches. Suggest 4-5 well-known, genuinely free (or free-to-audit) certifications/courses close to "
        "this topic, each with the provider name, in one short bullet line each. Keep it factual and concise."
    )
    return chat_completion(
        [{"role": "system", "content": "You are a career/learning advisor recommending real, well-known free certification providers."},
         {"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=350,
    )


def search_certifications(query: str, level="Any", duration_kw="", language="Any",
                           certificate="Any", free="Any"):
    """
    Main entry point for Module 1 — SAME PUBLIC SIGNATURE AND 4-KEY
    RETURN SHAPE AS BEFORE, so pages/15_🎓_Certifications_and_Jobs.py
    requires zero changes:
      {
        "live_results": [...],      # now from Microsoft Learn + (optional) Coursera, merged/deduped/ranked
        "search_links": [...],      # official provider search-endpoint cards (never empty) — now 17 providers
        "recommendation": str|None, # AI fallback text if live_results is thin
        "provider_status": {...},   # now uses the required status vocabulary for every provider
      }
    """
    provider_status = {}
    live_results = []
    q = query.strip()

    if q:
        # Microsoft Learn runs FIRST (it's the verified, primary live
        # source). Coursera runs SECOND and is explicitly optional — if
        # it fails, Microsoft Learn's results (plus every Tier 2 search
        # link) still make this a fully working, non-empty search.
        _run_live_provider("Microsoft Learn", "ms_learn", _fetch_microsoft_learn, q, provider_status, live_results)
        _run_live_provider("Coursera", "coursera", _fetch_coursera, q, provider_status, live_results)

    # Every Tier 2 (search-link) provider is always available — mark
    # them explicitly in provider_status for full architecture
    # transparency (previously provider_status only ever contained a
    # "Coursera" key; now every configured provider is visible in the
    # debug panel, including which tier it's operating at).
    for p in OFFICIAL_SEARCH_PROVIDERS:
        provider_status.setdefault(p["name"], STATUS_SEARCH_LINK)

    live_results = _dedupe_results(live_results)
    filtered_live = _apply_filters(live_results, level, duration_kw, language, certificate, free)
    ranked_live = _rank(filtered_live, query) if q else live_results

    search_links = _official_search_cards(q)

    recommendation = None
    if q and len(ranked_live) < 3:
        try:
            recommendation = _ai_recommend_similar(q)
        except Exception as e:
            logger.warning("AI recommendation fallback failed: %s", e)
            recommendation = None

    return {
        "live_results": ranked_live,
        "search_links": search_links,
        "recommendation": recommendation,
        "provider_status": provider_status,
    }
