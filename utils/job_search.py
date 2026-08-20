"""
utils/job_search.py
----------------------
Module 2: Green Job Finder — fully independent live-search module.

This module has NO dependency on any local job dataset and does NOT
import or call anything from utils/certification_search.py (Module 1) —
the two are independent so a failure/change in one never affects the
other.

Sources combined on every search:

1. LIVE OFFICIAL APIs
   - Adzuna Jobs API (official, free tier — requires ADZUNA_APP_ID /
     ADZUNA_APP_KEY; module works without them, just skips this source)
   - RemoteOK public JSON API (official, no key required)
   - Jooble API (official, free tier — requires JOOBLE_API_KEY)

2. OFFICIAL SEARCH ENDPOINTS — for boards without a public search API
   (LinkedIn, Naukri, Indeed, Internshala, Foundit, UN Careers, etc.) we
   build a direct link to that board's own documented search URL with
   filters pre-filled. This is navigation, not scraping.

Every provider call is cached, rate-limited, and wrapped in try/except
with a timeout, so one slow/unconfigured/failing provider never breaks
the page or the other providers.
"""
import time
import logging
import requests
import streamlit as st
from urllib.parse import quote

from config import settings
from utils.ai_client import chat_completion

logger = logging.getLogger("ecovision.jobsearch")

REQUEST_TIMEOUT = 8
CACHE_TTL_SECONDS = 300
MIN_SECONDS_BETWEEN_LIVE_CALLS = 3

# ---------------------------------------------------------------------
# Boards without a public search API: link straight to their own
# official search page using each site's documented URL pattern.
# ---------------------------------------------------------------------
OFFICIAL_SEARCH_BOARDS = [
    {"name": "LinkedIn", "url_tpl": "https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}"},
    {"name": "Naukri", "url_tpl": "https://www.naukri.com/{qslug}-jobs-in-{locslug}"},
    {"name": "Indeed", "url_tpl": "https://in.indeed.com/jobs?q={q}&l={loc}"},
    {"name": "Internshala", "url_tpl": "https://internshala.com/internships/keywords-{qslug}"},
    {"name": "Foundit (Monster)", "url_tpl": "https://www.foundit.in/srp/results?query={q}&locations={loc}"},
    {"name": "UN Careers", "url_tpl": "https://careers.un.org/"},
]


def _rate_limit_ok(key: str) -> bool:
    last = st.session_state.get(f"_job_rl_{key}", 0)
    now = time.time()
    if now - last < MIN_SECONDS_BETWEEN_LIVE_CALLS:
        return False
    st.session_state[f"_job_rl_{key}"] = now
    return True


# ---------------------------------------------------------------------
# LIVE PROVIDER 1: Adzuna (official API, free tier, requires keys)
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_adzuna(keyword: str, city: str, remote_only: bool, salary_min, app_id: str, app_key: str):
    if not app_id or not app_key:
        return []
    try:
        params = {
            "app_id": app_id, "app_key": app_key,
            "what": keyword or "sustainability",
            "results_per_page": 15,
            "content-type": "application/json",
        }
        if city:
            params["where"] = city
        if salary_min:
            params["salary_min"] = salary_min
        resp = requests.get(
            f"https://api.adzuna.com/v1/api/jobs/{settings.ADZUNA_COUNTRY}/search/1",
            params=params, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Adzuna API unavailable: %s", e)
        return []
    except (ValueError, KeyError) as e:
        logger.warning("Adzuna API returned unexpected data: %s", e)
        return []

    results = []
    for j in data.get("results", []):
        loc = (j.get("location") or {}).get("display_name", "")
        results.append({
            "title": j.get("title") or "Untitled Role",
            "company": (j.get("company") or {}).get("display_name", "Unknown"),
            "location": loc,
            "remote": "remote" in (j.get("title", "") + loc).lower(),
            "salary": _format_salary(j.get("salary_min"), j.get("salary_max")),
            "description": (j.get("description") or "")[:260],
            "url": j.get("redirect_url", "https://www.adzuna.in/"),
            "logo": "",
            "last_updated": j.get("created", "")[:10] if j.get("created") else None,
            "source": "Adzuna Official API",
        })
    return results


# ---------------------------------------------------------------------
# LIVE PROVIDER 2: RemoteOK (official public JSON API, no key required)
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_remoteok(keyword: str):
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "EcoVisionAI/1.0 (+https://ecovision-ai.streamlit.app)"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning("RemoteOK API unavailable: %s", e)
        return []
    except ValueError as e:
        logger.warning("RemoteOK API returned unexpected data: %s", e)
        return []

    if not isinstance(data, list) or len(data) < 2:
        return []

    kw = (keyword or "").lower()
    results = []
    for j in data[1:]:  # first element is a legal/meta notice, not a job
        if not isinstance(j, dict):
            continue
        text = f"{j.get('position','')} {j.get('company','')} {' '.join(j.get('tags') or [])}".lower()
        if kw and kw not in text:
            continue
        results.append({
            "title": j.get("position") or "Untitled Role",
            "company": j.get("company") or "Unknown",
            "location": "Remote",
            "remote": True,
            "salary": _format_salary(j.get("salary_min"), j.get("salary_max")),
            "description": (j.get("description") or "")[:260],
            "url": j.get("url") or "https://remoteok.com/",
            "logo": j.get("company_logo") or "",
            "last_updated": j.get("date", "")[:10] if j.get("date") else None,
            "source": "RemoteOK Official API",
        })
        if len(results) >= 15:
            break
    return results


# ---------------------------------------------------------------------
# LIVE PROVIDER 3: Jooble (official API, free tier, requires key)
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_jooble(keyword: str, city: str, api_key: str):
    if not api_key:
        return []
    try:
        resp = requests.post(
            f"https://jooble.org/api/{api_key}",
            json={"keywords": keyword or "sustainability", "location": city or "India"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning("Jooble API unavailable: %s", e)
        return []
    except (ValueError, KeyError) as e:
        logger.warning("Jooble API returned unexpected data: %s", e)
        return []

    results = []
    for j in data.get("jobs", []):
        results.append({
            "title": j.get("title") or "Untitled Role",
            "company": j.get("company") or "Unknown",
            "location": j.get("location") or "",
            "remote": "remote" in (j.get("title", "") + j.get("location", "")).lower(),
            "salary": j.get("salary") or "-",
            "description": (j.get("snippet") or "")[:260],
            "url": j.get("link", "https://jooble.org/"),
            "logo": "",
            "last_updated": j.get("updated", "")[:10] if j.get("updated") else None,
            "source": "Jooble Official API",
        })
    return results


def _format_salary(lo, hi):
    if not lo and not hi:
        return "-"
    if lo and hi:
        return f"₹{int(lo):,} - ₹{int(hi):,}"
    return f"₹{int(lo or hi):,}"


def _official_search_cards(keyword: str, city: str):
    q = quote(keyword or "sustainability jobs")
    loc = quote(city or "India")
    qslug = quote((keyword or "sustainability").replace(" ", "-"))
    locslug = quote((city or "india").replace(" ", "-"))
    cards = []
    for b in OFFICIAL_SEARCH_BOARDS:
        url = b["url_tpl"].format(q=q, loc=loc, qslug=qslug, locslug=locslug)
        cards.append({
            "title": f"Search '{keyword or 'green jobs'}' on {b['name']}",
            "company": b["name"], "location": city or "India", "remote": None,
            "salary": "-", "description": "Official job board search page — live listings open directly on their site.",
            "url": url, "logo": "", "last_updated": None, "source": "Official Search Endpoint",
        })
    return cards


def _apply_filters(results, city, state, remote_mode, experience_kw, salary_min):
    def keep(r):
        loc = str(r.get("location", "")).lower()
        if city and city.lower() not in loc and not r.get("remote"):
            return False
        if state and state.lower() not in loc and not r.get("remote"):
            return False
        if remote_mode == "Remote" and not r.get("remote"):
            return False
        if remote_mode == "On-site" and r.get("remote"):
            return False
        if experience_kw and experience_kw.lower() not in (r.get("title", "") + r.get("description", "")).lower():
            return False
        return True
    return [r for r in results if keep(r)]


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _ai_learning_path(role: str):
    prompt = (
        f"Suggest a short learning path (3-4 steps) and 3 in-demand skills for someone targeting a "
        f"'{role or 'sustainability'}' role in India's green economy. Keep it concise, bullet points only."
    )
    return chat_completion(
        [{"role": "system", "content": "You are a green-careers advisor for the Indian job market."},
         {"role": "user", "content": prompt}],
        temperature=0.5, max_tokens=300,
    )


def search_jobs(keyword: str, city="", state="", remote_mode="Any", experience_kw="",
                 salary_min=None, skills_kw=""):
    """
    Main entry point for Module 2. Always returns a non-empty payload:
      {
        "live_results": [...],
        "search_links": [...],       # never empty
        "recommendation": str|None,  # AI learning-path fallback if results are thin
        "provider_status": {"Adzuna": "...", "RemoteOK": "...", "Jooble": "..."},
      }
    """
    provider_status = {}
    combined_query = " ".join(filter(None, [keyword, skills_kw])).strip()
    live_results = []

    if settings.is_adzuna_configured():
        if _rate_limit_ok("adzuna"):
            r = _fetch_adzuna(combined_query, city, remote_mode == "Remote", salary_min,
                              settings.ADZUNA_APP_ID, settings.ADZUNA_APP_KEY)
            live_results += r
            provider_status["Adzuna"] = "ok" if r else "no_results"
        else:
            provider_status["Adzuna"] = "rate_limited"
    else:
        provider_status["Adzuna"] = "not_configured"

    if _rate_limit_ok("remoteok"):
        r = _fetch_remoteok(combined_query)
        live_results += r
        provider_status["RemoteOK"] = "ok" if r else "no_results"
    else:
        provider_status["RemoteOK"] = "rate_limited"

    if settings.is_jooble_configured():
        if _rate_limit_ok("jooble"):
            r = _fetch_jooble(combined_query, city, settings.JOOBLE_API_KEY)
            live_results += r
            provider_status["Jooble"] = "ok" if r else "no_results"
        else:
            provider_status["Jooble"] = "rate_limited"
    else:
        provider_status["Jooble"] = "not_configured"

    filtered = _apply_filters(live_results, city, state, remote_mode, experience_kw, salary_min)

    search_links = _official_search_cards(keyword, city)

    recommendation = None
    if len(filtered) < 3:
        try:
            recommendation = _ai_learning_path(keyword)
        except Exception as e:
            logger.warning("AI learning-path fallback failed: %s", e)
            recommendation = None

    return {
        "live_results": filtered,
        "search_links": search_links,
        "recommendation": recommendation,
        "provider_status": provider_status,
    }