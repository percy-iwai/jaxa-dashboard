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
    _kits = sorted(Path(__file__).parent.glob("jaxa_dashboard_kit_*.zip"))
    _kit = _kits[-1] if _kits else None
    if _kit:
        st.download_button(
            label="📦 宇宙調達 kit をダウンロード",
            data=_kit.read_bytes(),
            file_name=_kit.name,
            mime="application/zip",
            help="JAXAダッシュボード・DB・パイプライン一式",
        )
    else:
        st.info("ローカル環境でのみ\nダウンロード可能です", icon="💻")
    _guide = Path(__file__).parent / "review_api_guide.md"
    if _guide.exists():
        st.download_button(
            label="📝 行政事業レビュー取得手順（.md）",
            data=_guide.read_bytes(),
            file_name="行政事業レビュー_データ取得とダッシュボード_指示書.md",
            mime="text/markdown",
            help="rssystem.go.jp API からの取得方法と HTML ダッシュボードの作り方（AIエージェント向け指示書）",
        )
    _twbx = Path(__file__).parent / "jaxa_space_review.twbx"
    if _twbx.exists():
        st.download_button(
            label="📊 Tableauワークブック（.twbx）",
            data=_twbx.read_bytes(),
            file_name=_twbx.name,
            mime="application/octet-stream",
            help="宇宙関連行政事業レビューの Tableau パッケージドワークブック（データ jaxa_space_review.csv 同梱）",
        )

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
