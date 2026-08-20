import streamlit as st
from utils.ai_client import generate_awareness_content
from utils.helpers import load_css, init_session_state
from config import settings

st.set_page_config(page_title="Awareness Hub | EcoVision AI", page_icon="🌱", layout="wide")
init_session_state()
load_css()

st.markdown('<div class="eco-hero"><h1>🌱 Awareness Hub</h1><p>AI-generated eco tips, campaign ideas, and school awareness content for sustainability drives.</p></div>', unsafe_allow_html=True)

if not settings.is_ai_configured():
    st.warning("Demo mode — add OPENROUTER_API_KEY to .env for live AI-generated content.")

content_type = st.selectbox("What do you want to generate?",
                             ["Eco Tips", "Awareness Poster Text", "Campaign Ideas", "School Awareness Activity"])
topic = st.text_input("Topic", placeholder="e.g. plastic-free monsoon, e-waste week, composting drive")

if st.button("✨ Generate", type="primary"):
    with st.spinner("Generating content..."):
        content = generate_awareness_content(topic or "waste management awareness", content_type.lower())
    st.markdown(f'<div class="eco-card">{content}</div>', unsafe_allow_html=True)
    st.download_button("Download as text", content.encode(), f"{content_type.replace(' ', '_').lower()}.txt")

st.markdown('<div class="eco-section-title">📰 Latest Awareness Themes</div>', unsafe_allow_html=True)
themes = [
    ("🧹", "Swachh Bharat Mission", "India's flagship cleanliness and sanitation movement."),
    ("🚫", "Single-Use Plastic Ban", "National guidelines on banned plastic items and alternatives."),
    ("🌱", "Composting at Home", "Simple methods for apartment and household composting."),
    ("🔋", "E-Waste Awareness Week", "Safe disposal drives for batteries and electronics."),
]
cols = st.columns(4)
for col, (icon, title, desc) in zip(cols, themes):
    with col:
        st.markdown(f'<div class="eco-card"><div style="font-size:1.8rem;">{icon}</div><b>{title}</b><p style="color:#94a3b8;font-size:0.85rem;">{desc}</p></div>', unsafe_allow_html=True)
