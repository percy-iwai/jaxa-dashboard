"""
宇宙関連予算ダッシュボード
起動: streamlit run app.py
"""

import streamlit as st

st.set_page_config(
    page_title="宇宙関連予算ダッシュボード",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

import jaxa_dashboard
import review_dashboard
import fund_dashboard

tab1, tab2, tab3 = st.tabs([
    "🚀 JAXA契約実績",
    "📋 行政事業レビュー 宇宙関連",
    "💰 宇宙戦略基金",
])

with tab1:
    jaxa_dashboard.show()

with tab2:
    review_dashboard.show()

with tab3:
    fund_dashboard.show()
