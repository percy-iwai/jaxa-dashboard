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

from pathlib import Path

import jaxa_dashboard
import review_dashboard
import fund_dashboard

with st.sidebar:
    st.caption("🗂️ 引っ越しキット")
    _kit = Path(__file__).parent.parent / "jaxa_dashboard_kit_20260613.zip"
    if _kit.exists():
        st.download_button(
            label="📦 宇宙調達 kit をダウンロード",
            data=_kit.read_bytes(),
            file_name="jaxa_dashboard_kit_20260613.zip",
            mime="application/zip",
            help="JAXAダッシュボード・DB・パイプライン一式",
        )
    else:
        st.info("ローカル環境でのみ\nダウンロード可能です", icon="💻")

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
