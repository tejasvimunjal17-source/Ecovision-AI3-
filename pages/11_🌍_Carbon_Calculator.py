import streamlit as st
import plotly.express as px
import pandas as pd
from database.db_router import execute
from utils.ai_client import chat_completion
from utils.helpers import load_css, init_session_state

st.set_page_config(page_title="Carbon Calculator | EcoVision AI", page_icon="🌍", layout="wide")
init_session_state()
load_css()

st.markdown('<div class="eco-hero"><h1>🌍 Carbon Footprint Calculator</h1><p>Estimate your monthly carbon footprint across key lifestyle categories.</p></div>', unsafe_allow_html=True)

st.markdown("Fill in your approximate monthly usage. Emission factors are simplified India-average estimates for educational purposes.")

c1, c2 = st.columns(2)
with c1:
    transport_km = st.slider("🚗 Monthly distance by car/bike (km)", 0, 3000, 300)
    electricity_kwh = st.slider("⚡ Monthly electricity usage (kWh)", 0, 1000, 200)
    plastic_kg = st.slider("🧴 Monthly plastic waste generated (kg)", 0, 50, 5)
with c2:
    water_liters = st.slider("💧 Daily water usage (liters)", 0, 500, 150)
    food_meat_meals = st.slider("🍖 Non-vegetarian meals per week", 0, 21, 4)
    waste_kg = st.slider("🗑️ Weekly general waste generated (kg)", 0, 50, 7)

# Simplified emission factors (kg CO2e) — for education/estimation only
transport_co2 = transport_km * 0.14
electricity_co2 = electricity_kwh * 0.82
plastic_co2 = plastic_kg * 6.0
water_co2 = (water_liters * 30 / 1000) * 0.34
food_co2 = food_meat_meals * 4 * 3.3
waste_co2 = waste_kg * 4 * 0.5

total_co2 = round(transport_co2 + electricity_co2 + plastic_co2 + water_co2 + food_co2 + waste_co2, 1)

st.markdown('<div class="eco-section-title">📊 Your Estimated Footprint</div>', unsafe_allow_html=True)
st.markdown(f'<div class="eco-stat"><div class="num">{total_co2} kg CO₂e</div><div class="label">Estimated Monthly Footprint</div></div>', unsafe_allow_html=True)

breakdown = pd.DataFrame({
    "Category": ["Transport", "Electricity", "Plastic", "Water", "Food", "Waste"],
    "kg CO2e": [transport_co2, electricity_co2, plastic_co2, water_co2, food_co2, waste_co2],
})
fig = px.pie(breakdown, names="Category", values="kg CO2e", title="Footprint Breakdown", hole=0.4)
st.plotly_chart(fig, use_container_width=True)

if total_co2 < 150:
    score = "🟢 Excellent — well below average"
elif total_co2 < 300:
    score = "🟡 Good — around India average"
else:
    score = "🔴 High — room for improvement"
st.info(f"**Carbon Score:** {score}")

if st.button("🤖 Get AI Reduction Suggestions", type="primary"):
    prompt = (
        f"A user in an Indian city has this monthly carbon footprint breakdown (kg CO2e): "
        f"Transport {transport_co2:.0f}, Electricity {electricity_co2:.0f}, Plastic {plastic_co2:.0f}, "
        f"Water {water_co2:.0f}, Food {food_co2:.0f}, Waste {waste_co2:.0f}. "
        "Give 5 short, practical, India-specific tips to reduce their footprint, as bullet points."
    )
    with st.spinner("Analyzing..."):
        tips = chat_completion(
            [{"role": "system", "content": "You are a sustainability coach."},
             {"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=400,
        )
    st.markdown(f'<div class="eco-card">{tips}</div>', unsafe_allow_html=True)

user = st.session_state.get("user")
if user and st.button("💾 Save this record to my profile"):
    execute(
        """INSERT INTO carbon_records (user_id, transport_kg, electricity_kg, plastic_kg, water_kg,
           food_kg, waste_kg, total_score) VALUES (?,?,?,?,?,?,?,?)""",
        (user["id"], transport_co2, electricity_co2, plastic_co2, water_co2, food_co2, waste_co2, total_co2),
    )
    st.success("Saved!")
