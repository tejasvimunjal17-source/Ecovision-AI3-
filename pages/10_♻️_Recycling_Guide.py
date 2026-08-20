import streamlit as st
from database.db_router import fetch_all
from utils.ai_client import generate_awareness_content
from utils.helpers import load_css, init_session_state
from config import settings

st.set_page_config(page_title="Recycling Guide | EcoVision AI", page_icon="♻️", layout="wide")
init_session_state()
load_css()

st.markdown('<div class="eco-hero"><h1>♻️ Waste Segregation & Recycling Guide</h1><p>Know exactly how to segregate and dispose of every category of household waste.</p></div>', unsafe_allow_html=True)

categories = fetch_all("SELECT * FROM categories WHERE is_active=1")

for row_start in range(0, len(categories), 3):
    cols = st.columns(3)
    for col, cat in zip(cols, categories[row_start:row_start + 3]):
        with col:
            st.markdown(
                f"""<div class="eco-card">
                    <div style="font-size:2rem;">{cat['icon'] or '🗑️'}</div>
                    <div style="font-weight:700;font-size:1.1rem;margin:0.3rem 0;">{cat['name']}</div>
                    <div style="color:#94a3b8;font-size:0.88rem;margin-bottom:0.5rem;">{cat['description']}</div>
                    <div style="color:#34d399;font-size:0.85rem;"><b>Disposal:</b> {cat['disposal_guide']}</div>
                </div>""",
                unsafe_allow_html=True,
            )

st.markdown('<div class="eco-section-title">🤖 Ask AI for a Custom Tip</div>', unsafe_allow_html=True)
topic = st.text_input("What would you like tips on?", placeholder="e.g. composting kitchen waste in an apartment")
if st.button("Generate Tips", type="primary"):
    if not settings.is_ai_configured():
        st.warning("Demo mode — add OPENROUTER_API_KEY to .env for live AI tips.")
    with st.spinner("Generating..."):
        content = generate_awareness_content(topic or "general waste segregation", "eco tips")
    st.markdown(f'<div class="eco-card">{content}</div>', unsafe_allow_html=True)
