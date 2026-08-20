import streamlit as st
import pandas as pd
from backend.complaints import get_user_rewards, get_leaderboard
from utils.helpers import load_css, require_login, format_datetime

st.set_page_config(page_title="Rewards | EcoVision AI", page_icon="🏆", layout="wide")
require_login(allowed_roles=["citizen"])
load_css()

user = st.session_state["user"]
st.markdown('<div class="eco-hero"><h1>🏆 Green Rewards</h1><p>Earn points for responsible reporting and climb the city leaderboard.</p></div>', unsafe_allow_html=True)

st.markdown(f'<div class="eco-stat"><div class="num">{user["reward_points"]}</div><div class="label">Your Total Points</div></div>', unsafe_allow_html=True)

tab1, tab2 = st.tabs(["📜 My Points History", "🥇 City Leaderboard"])

with tab1:
    rewards = get_user_rewards(user["id"])
    if not rewards:
        st.info("Start reporting waste to earn your first reward points!")
    else:
        for r in rewards:
            st.markdown(f"- `{format_datetime(r['created_at'])}` — **+{r['points']} pts** — {r['reason']}")

with tab2:
    leaderboard = get_leaderboard(20)
    if leaderboard:
        df = pd.DataFrame(leaderboard)
        df.index = df.index + 1
        df.rename(columns={"full_name": "Citizen", "ward": "Ward", "reward_points": "Points"}, inplace=True)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No leaderboard data yet.")
