"""
pages/15_🎓_Certifications_and_Jobs.py
------------------------------------------
Hosts two INDEPENDENT live-search modules side by side as tabs:

  Tab 1 -> utils/certification_search.py  (Module 1: Certification Finder)
  Tab 2 -> utils/job_search.py            (Module 2: Green Job Finder)
  Tab 3 -> AI Resume Generator (unchanged, unrelated feature)

Each module tab is wrapped in its own try/except at the page level as a
second line of defense on top of each module's internal error handling —
so even an unexpected bug in one module's rendering code can never take
down the other tab.
"""
import streamlit as st
from utils.ai_client import generate_resume_content
from utils.helpers import load_css, init_session_state
from config import settings

st.set_page_config(page_title="Certifications & Green Jobs | EcoVision AI", page_icon="🎓", layout="wide")
init_session_state()
load_css()

st.markdown(
    '<div class="eco-hero"><h1>🎓 Free Certifications & 🌱 Green Job Search</h1>'
    '<p>Live search across official providers — no local datasets, always up to date.</p></div>',
    unsafe_allow_html=True,
)

st.info(
    "ℹ️ Results combine **live official APIs** (where available) with direct links to each "
    "provider's **own official search page** for platforms that don't expose a public API. "
    "We never scrape sites that prohibit it."
)

tab_certs, tab_jobs, tab_resume = st.tabs(["🎓 Certification Finder", "🌱 Green Job Finder", "📄 AI Resume Generator"])


def _safe_html(html: str) -> str:
    """
    Collapse a (possibly multi-line, indented) HTML snippet to a single
    line before handing it to st.markdown().

    Root cause this guards against: when a placeholder like {logo_html}
    sits alone on its own line and evaluates to an empty string, that
    line becomes whitespace-only. Markdown's HTML-block rule treats a
    blank line as the end of the current raw-HTML block, so everything
    after it gets reparsed as an *indented code block* (the source lines
    are still indented 8-12 spaces) — which HTML-escapes every tag,
    showing literal `</div>` etc. as visible text instead of rendering.
    Joining everything onto one line makes a blank line structurally
    impossible and removes all leading indentation, so this can't happen
    regardless of which optional fields are empty. Visual output is
    unchanged since HTML collapses whitespace between tags anyway.
    """
    return " ".join(line.strip() for line in html.strip().splitlines())


def _render_result_card(title, provider_or_company, description, meta_line, url, logo="", last_updated=None, source=None):
    logo_html = f'<img src="{logo}" style="height:32px;border-radius:6px;margin-bottom:4px;" onerror="this.style.display=\'none\'">' if logo else ""
    updated_html = f'<br><span style="color:#64748b;font-size:0.78rem;">🕒 Last updated: {last_updated}</span>' if last_updated else ""
    source_html = f'<span style="color:#38bdf8;font-size:0.75rem;">· {source}</span>' if source else ""
    html = f"""<div class="eco-card">
            {logo_html}
            <b>{title}</b> — <span style="color:#38bdf8;">{provider_or_company}</span> {source_html}<br>
            <span style="color:#94a3b8;font-size:0.85rem;">{description}</span><br>
            <span style="font-size:0.85rem;">{meta_line}</span>
            {updated_html}<br>
            <a href="{url}" target="_blank" style="color:#34d399;">Open official page →</a>
        </div>"""
    st.markdown(_safe_html(html), unsafe_allow_html=True)


# =====================================================================
# TAB 1 — MODULE 1: CERTIFICATION FINDER (independent module)
# =====================================================================
with tab_certs:
    try:
        from utils.certification_search import search_certifications

        with st.form("cert_search_form"):
            query = st.text_input("Search by skill, topic, or provider", placeholder="e.g. cloud computing, sustainability, python")
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                level_f = st.selectbox("Level", ["Any", "Beginner", "Intermediate", "Advanced"])
            with c2:
                duration_f = st.text_input("Duration keyword", placeholder="e.g. weeks, hrs")
            with c3:
                language_f = st.selectbox("Language", ["Any", "English", "Hindi"])
            with c4:
                certificate_f = st.selectbox("Certificate", ["Any", "Yes", "No"])
            with c5:
                free_f = st.selectbox("Free/Paid", ["Any", "Free", "Paid"])
            cert_submit = st.form_submit_button("🔍 Search Certifications", type="primary", use_container_width=True)

        if cert_submit or "_cert_last_query" in st.session_state:
            if cert_submit:
                st.session_state["_cert_last_query"] = dict(
                    query=query, level=level_f, duration_kw=duration_f,
                    language=language_f, certificate=certificate_f, free=free_f,
                )
            params = st.session_state["_cert_last_query"]

            with st.spinner("Searching official providers..."):
                result = search_certifications(**params)

            live = result["live_results"]
            links = result["search_links"]

            if live:
                st.success(f"✅ Found {len(live)} live result(s) from official APIs.")
                for r in live:
                    _render_result_card(
                        r["title"], r["provider"], r["description"],
                        f"📈 {r['level']} · ⏱️ {r['duration']} · 🌐 {r['language']} · "
                        f"{'🎓 Certificate' if r['certificate'] else '📘 —' if r['certificate'] is False else ''}",
                        r["url"], r.get("logo", ""), r.get("last_updated"), r.get("source"),
                    )
            else:
                st.warning("No direct live-API matches for this query yet — but here's where to search directly, plus similar recommendations below.")

            if result["recommendation"]:
                st.markdown('<div class="eco-section-title">🤖 Recommended Similar Certifications</div>', unsafe_allow_html=True)
                st.markdown(_safe_html(f'<div class="eco-card">{result["recommendation"]}</div>'), unsafe_allow_html=True)

            st.markdown('<div class="eco-section-title">🌐 Search Directly on Official Provider Sites</div>', unsafe_allow_html=True)
            cols = st.columns(3)
            for i, link in enumerate(links):
                with cols[i % 3]:
                    _render_result_card(link["title"], link["provider"], link["description"], "", link["url"], source=link["source"])

            with st.expander("🔧 Provider status (debug)"):
                st.json(result["provider_status"])
        else:
            st.info("👆 Enter a search above — Coursera's live catalog is queried directly, and every major free-certification provider is one click away below.")

    except Exception as e:
        st.error("⚠️ The Certification Finder hit an unexpected error and couldn't load. This won't affect the Job Finder tab.")
        st.caption(f"Technical detail: {e}")


# =====================================================================
# TAB 2 — MODULE 2: GREEN JOB FINDER (independent module)
# =====================================================================
with tab_jobs:
    try:
        from utils.job_search import search_jobs

        if not settings.is_adzuna_configured() and not settings.is_jooble_configured():
            st.caption("ℹ️ Adzuna/Jooble API keys aren't configured — RemoteOK (no key needed) and official "
                       "search links still work fully. Add ADZUNA_APP_ID/ADZUNA_APP_KEY or JOOBLE_API_KEY "
                       "to `.env` for broader live coverage.")

        with st.form("job_search_form"):
            role_q = st.text_input("Role / Keyword", placeholder="e.g. Sustainability Analyst")
            j1, j2, j3 = st.columns(3)
            with j1:
                city_q = st.text_input("City", placeholder="e.g. Gurugram")
            with j2:
                state_q = st.text_input("State", placeholder="e.g. Haryana")
            with j3:
                remote_f = st.selectbox("Work Mode", ["Any", "Remote", "Hybrid", "On-site"])
            j4, j5, j6 = st.columns(3)
            with j4:
                experience_q = st.text_input("Experience keyword", placeholder="e.g. entry-level, senior")
            with j5:
                salary_q = st.number_input("Minimum salary (optional)", min_value=0, step=50000, value=0)
            with j6:
                skills_q = st.text_input("Skills", placeholder="e.g. GIS, data analysis")
            job_submit = st.form_submit_button("🔍 Search Green Jobs", type="primary", use_container_width=True)

        if job_submit or "_job_last_query" in st.session_state:
            if job_submit:
                st.session_state["_job_last_query"] = dict(
                    keyword=role_q, city=city_q, state=state_q, remote_mode=remote_f,
                    experience_kw=experience_q, salary_min=salary_q or None, skills_kw=skills_q,
                )
            params = st.session_state["_job_last_query"]

            with st.spinner("Searching official job APIs and boards..."):
                result = search_jobs(**params)

            live = result["live_results"]
            links = result["search_links"]

            if live:
                st.success(f"✅ Found {len(live)} live job(s) from official APIs.")
                for r in live:
                    mode = "🏠 Remote" if r.get("remote") else "🏢 On-site/Hybrid"
                    _render_result_card(
                        r["title"], r["company"], r["description"],
                        f"📍 {r['location']} · {mode} · 💰 {r['salary']}",
                        r["url"], r.get("logo", ""), r.get("last_updated"), r.get("source"),
                    )
            else:
                st.warning("No exact live matches yet — here are related search links and a learning path below.")

            if result["recommendation"]:
                st.markdown('<div class="eco-section-title">🎯 AI Career Tip & Learning Path</div>', unsafe_allow_html=True)
                st.markdown(_safe_html(f'<div class="eco-card">{result["recommendation"]}</div>'), unsafe_allow_html=True)

            st.markdown('<div class="eco-section-title">🌐 Search Directly on Official Job Boards</div>', unsafe_allow_html=True)
            for link in links:
                _render_result_card(link["title"], link["company"], link["description"], "", link["url"], source=link["source"])

            with st.expander("🔧 Provider status (debug)"):
                st.json(result["provider_status"])
        else:
            st.info("👆 Enter your search above — RemoteOK is queried live with no setup required, and every "
                    "major job board is one click away below.")

    except Exception as e:
        st.error("⚠️ The Green Job Finder hit an unexpected error and couldn't load. This won't affect the Certification Finder tab.")
        st.caption(f"Technical detail: {e}")


# =====================================================================
# TAB 3 — AI Resume Generator (unchanged, unrelated to the dataset fix)
# =====================================================================
with tab_resume:
    kind = st.selectbox("Generate", ["resume", "cover_letter", "portfolio", "linkedin"],
                         format_func=lambda k: {"resume": "Resume", "cover_letter": "Cover Letter",
                                                 "portfolio": "Portfolio Summary", "linkedin": "LinkedIn Summary"}[k])
    profile = st.text_area("Paste your details (education, experience, skills, target role)", height=200,
                            placeholder="e.g. B.Tech Environmental Engineering, 2 internships in waste management, skilled in GIS and data analysis, targeting a Sustainability Analyst role...")
    if st.button("✨ Generate", type="primary", key="resume_gen"):
        if not profile.strip():
            st.warning("Please add some profile details first.")
        else:
            with st.spinner("Writing..."):
                content = generate_resume_content(kind, profile)
            st.markdown(content)
            st.download_button("Download as text", content.encode(), f"{kind}.md")
