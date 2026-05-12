"""
JAXA 調達DB ダッシュボード
データソース: data/db/jaxa_procurement.db (contracts テーブル)
対象期間: FY2019〜FY2024
"""

import io
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "db" / "jaxa_procurement.db"
TEMPLATE = "plotly_dark"

CATEGORY_ORDER = [
    "宇宙機・衛星",
    "ロケット・打上",
    "その他",
    "研究・開発",
    "試験・評価・解析",
    "施設・設備・工事",
    "物品・機器・部品",
    "情報システム・IT",
    "役務・委託",
    "輸送・物流",
]

CATEGORY_COLORS = {
    "宇宙機・衛星":     "#4472c4",
    "ロケット・打上":   "#ed7d31",
    "研究・開発":       "#70ad47",
    "その他":           "#7f7f7f",
    "試験・評価・解析": "#9e2a2b",
    "施設・設備・工事": "#ffc000",
    "物品・機器・部品": "#5e8c61",
    "情報システム・IT": "#8e44ad",
    "役務・委託":       "#0070c0",
    "輸送・物流":       "#833c00",
}

VENDOR_NORMALIZE: dict[str, str] = {
    "BlueOrigin,LLC": "Blue Origin, LLC",
}

_CORP_FORMS = [
    ("株式会社", [r"（株）", r"\(株\)"]),
    ("有限会社", [r"（有）", r"\(有\)"]),
    ("合同会社", [r"（合）", r"\(合\)"]),
]


def _normalize_company(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        return name
    s = unicodedata.normalize("NFKC", name.strip())
    s = re.sub(r"[\s　]+", "", s)
    for full, abbr_pats in _CORP_FORMS:
        s = re.sub(r"^" + full + r"(.+)$", r"\g<1>" + full, s)
        for abbr in abbr_pats:
            s = re.sub(r"^" + abbr + r"(.+)$", r"\g<1>" + full, s)
            s = re.sub(r"^(.+)" + abbr + r"$", r"\g<1>" + full, s)
    return VENDOR_NORMALIZE.get(s, s)


_CSS = """
<style>
  #MainMenu {visibility: hidden;}
  footer {visibility: hidden;}
  header {visibility: hidden;}
  div[data-testid="metric-container"] {
    background: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 14px 18px;
  }
  div[data-testid="metric-container"] label {
    font-size: 0.78rem;
    color: #a6b0cf;
  }
  .stTabs [data-baseweb="tab"] {font-size: 0.9rem;}
  .block-container {padding-top: 1.5rem;}
</style>
"""


# ── データ読み込み ─────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        """
        SELECT
            id, fiscal_year, contract_name, contract_officer,
            contract_date, vendor_name, vendor_address, corporate_number,
            bid_method, zuii_reason, estimated_price, contract_amount,
            award_rate, category, source_url
        FROM contracts
        """,
        conn,
    )
    conn.close()
    df["contract_dt"] = pd.to_datetime(df["contract_date"], format="%Y%m%d", errors="coerce")
    df["contract_month"] = df["contract_dt"].dt.month
    df["vendor_name"] = df["vendor_name"].apply(_normalize_company)
    return df


# ── ヘルパー ───────────────────────────────────────────────────────

def fmt_oku(val) -> str:
    if val is None or pd.isna(val):
        return "N/A"
    v = float(val)
    if v >= 1e8:
        return f"{v/1e8:,.1f}億円"
    if v >= 1e4:
        return f"{v/1e4:,.0f}万円"
    return f"{v:,.0f}円"


def fmt_man(val) -> str:
    if val is None or pd.isna(val):
        return "N/A"
    return f"{float(val)/1e4:,.0f}万円"


# ── メイン show() ─────────────────────────────────────────────────

def show() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    raw = load_data()
    if raw.empty:
        st.error(f"DB が見つかりません: {DB_PATH}")
        return

    # ── サイドバー フィルター ───────────────────────────────────
    with st.sidebar:
        st.header("🔍 JAXA フィルター")

        all_years = sorted(raw["fiscal_year"].dropna().unique().astype(int).tolist())
        sel_years = st.multiselect("年度", all_years, default=all_years)

        all_cats = [c for c in CATEGORY_ORDER if c in raw["category"].unique()] + \
                   [c for c in raw["category"].dropna().unique() if c not in CATEGORY_ORDER]
        sel_cats = st.multiselect("カテゴリ", all_cats, default=all_cats)

        bid_opts = sorted(raw["bid_method"].dropna().unique().tolist())
        sel_bid = st.multiselect("契約方式", bid_opts, default=bid_opts)

        amt_max = int(raw["contract_amount"].max(skipna=True) or 0)
        amt_max_man = max(1, amt_max // 10_000)
        sel_amount = st.slider("契約金額（万円）", 0, amt_max_man, (0, amt_max_man))

        keyword = st.text_input("キーワード（件名・企業名）", "")

    # ── フィルター適用 ─────────────────────────────────────────
    df = raw.copy()
    if sel_years:
        df = df[df["fiscal_year"].isin(sel_years)]
    if sel_cats:
        df = df[df["category"].isin(sel_cats)]
    if sel_bid:
        df = df[df["bid_method"].isin(sel_bid)]
    df = df[
        df["contract_amount"].isna() |
        df["contract_amount"].between(sel_amount[0] * 10_000, sel_amount[1] * 10_000)
    ]
    if keyword:
        mask = (
            df["contract_name"].str.contains(keyword, na=False) |
            df["vendor_name"].str.contains(keyword, na=False)
        )
        df = df[mask]

    df_amt = df[df["contract_amount"].notna()]
    total_oku = df_amt["contract_amount"].sum() / 1e8

    # ── タイトル ─────────────────────────────────────────────
    st.title("🚀 JAXA 調達情報ダッシュボード")
    st.caption(
        "出典: 宇宙航空研究開発機構（JAXA）公開調達情報（会計法に基づく公表データ）"
        f" ／ 対象期間: FY{min(all_years)}〜FY{max(all_years)}  ／  全{len(raw):,}件"
    )

    # ── KPI 行 ───────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("総契約件数", f"{len(df):,}件")
    c2.metric("総契約金額", f"{total_oku:,.0f}億円")

    if len(df) > 0:
        zuii_cnt = df["bid_method"].str.contains("随意", na=False).sum()
        c3.metric("随意契約率", f"{zuii_cnt / len(df) * 100:.1f}%",
                  delta=f"{zuii_cnt:,}件 / {len(df):,}件", delta_color="off")
    else:
        c3.metric("随意契約率", "—")

    c4.metric("平均契約金額", fmt_man(df_amt["contract_amount"].mean()) if not df_amt.empty else "—")

    fy24 = raw[(raw["fiscal_year"] == 2024) & raw["contract_amount"].notna()]
    c5.metric("FY2024 総額", f"{fy24['contract_amount'].sum()/1e8:,.0f}億円",
              delta=f"{len(fy24):,}件", delta_color="off")

    st.divider()

    # ── タブ ─────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📊 推移・カテゴリ", "🏭 企業分析", "📋 契約方式", "🔎 データ閲覧"]
    )

    # ═══════════════════════════════════════════════════════════
    # タブ1: 推移・カテゴリ
    # ═══════════════════════════════════════════════════════════
    with tab1:
        if df_amt.empty:
            st.info("該当データがありません。")
        else:
            # 年度×カテゴリ 積み上げ棒グラフ
            agg = (
                df_amt.groupby(["fiscal_year", "category"])["contract_amount"]
                .sum()
                .reset_index()
            )
            agg["金額_億円"] = agg["contract_amount"] / 1e8
            agg["category"] = pd.Categorical(
                agg["category"],
                categories=[c for c in CATEGORY_ORDER if c in agg["category"].values] +
                            [c for c in agg["category"].unique() if c not in CATEGORY_ORDER],
                ordered=True,
            )
            fig_trend = px.bar(
                agg.sort_values(["fiscal_year", "category"]),
                x="fiscal_year", y="金額_億円",
                color="category",
                color_discrete_map=CATEGORY_COLORS,
                labels={"fiscal_year": "年度", "金額_億円": "契約金額（億円）", "category": "カテゴリ"},
                title="年度別・カテゴリ別 契約金額",
                barmode="stack",
                template=TEMPLATE,
                category_orders={"category": CATEGORY_ORDER},
            )
            fig_trend.update_layout(height=420, legend_title_text="カテゴリ")
            st.plotly_chart(fig_trend, use_container_width=True)

            # 年度別件数折れ線
            cnt_agg = df.groupby("fiscal_year").size().reset_index(name="件数")
            fig_cnt = px.line(
                cnt_agg, x="fiscal_year", y="件数",
                markers=True,
                labels={"fiscal_year": "年度", "件数": "契約件数"},
                title="年度別 契約件数推移",
                template=TEMPLATE,
            )
            fig_cnt.update_layout(height=300)
            st.plotly_chart(fig_cnt, use_container_width=True)

            st.divider()

            # カテゴリ別内訳（2列）
            col_pie, col_bar = st.columns(2)
            cat_agg = (
                df_amt.groupby("category")["contract_amount"]
                .sum()
                .reset_index()
                .sort_values("contract_amount", ascending=False)
            )
            cat_agg["金額_億円"] = cat_agg["contract_amount"] / 1e8

            with col_pie:
                fig_pie = px.pie(
                    cat_agg,
                    values="金額_億円",
                    names="category",
                    title="カテゴリ別 金額内訳",
                    color="category",
                    color_discrete_map=CATEGORY_COLORS,
                    template=TEMPLATE,
                )
                fig_pie.update_traces(textposition="inside", textinfo="percent+label")
                fig_pie.update_layout(showlegend=False, height=400)
                st.plotly_chart(fig_pie, use_container_width=True)

            with col_bar:
                fig_cat = px.bar(
                    cat_agg,
                    x="金額_億円", y="category",
                    orientation="h",
                    color="category",
                    color_discrete_map=CATEGORY_COLORS,
                    title="カテゴリ別 総契約金額",
                    labels={"金額_億円": "総額（億円）", "category": "カテゴリ"},
                    template=TEMPLATE,
                    text=[f"{v:,.0f}億" for v in cat_agg["金額_億円"]],
                )
                fig_cat.update_traces(textposition="outside")
                fig_cat.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    showlegend=False,
                    height=400,
                )
                st.plotly_chart(fig_cat, use_container_width=True)

    # ═══════════════════════════════════════════════════════════
    # タブ2: 企業分析
    # ═══════════════════════════════════════════════════════════
    with tab2:
        df_v = df_amt[df_amt["vendor_name"].notna()]
        vendor_agg = (
            df_v.groupby("vendor_name")
            .agg(
                件数=("contract_amount", "count"),
                総額=("contract_amount", "sum"),
            )
            .reset_index()
            .sort_values("総額", ascending=False)
        )
        vendor_agg["総額_億円"] = vendor_agg["総額"] / 1e8
        top20 = vendor_agg.head(20)

        fig_v = px.bar(
            top20,
            x="総額_億円", y="vendor_name",
            orientation="h",
            color="総額_億円",
            color_continuous_scale="Blues",
            title="企業別 総契約金額ランキング（上位20社）",
            labels={"総額_億円": "総契約金額（億円）", "vendor_name": "企業名"},
            text=[f"{v:,.0f}億" for v in top20["総額_億円"]],
            template=TEMPLATE,
        )
        fig_v.update_traces(textposition="outside")
        fig_v.update_layout(yaxis={"categoryorder": "total ascending"}, height=680, showlegend=False)
        st.plotly_chart(fig_v, use_container_width=True)

        # ドリルダウン
        top50_names = vendor_agg.head(50)["vendor_name"].tolist()
        sel_vendor = st.selectbox(
            "企業を選択してドリルダウン",
            ["（選択してください）"] + top50_names,
            key="jaxa_vendor_drilldown",
        )
        if sel_vendor != "（選択してください）":
            df_sel = df_v[df_v["vendor_name"] == sel_vendor]
            fy_trend = (
                df_sel.groupby("fiscal_year")
                .agg(件数=("contract_amount", "count"), 総額=("contract_amount", "sum"))
                .reset_index()
            )
            fy_trend["総額_億円"] = fy_trend["総額"] / 1e8

            col_tr, col_stat = st.columns([2, 1])
            with col_tr:
                fig_dd = px.bar(
                    fy_trend, x="fiscal_year", y="総額_億円",
                    title=f"{sel_vendor} — 年度別契約金額",
                    labels={"fiscal_year": "年度", "総額_億円": "契約金額（億円）"},
                    template=TEMPLATE,
                    text=[f"{v:.1f}億" for v in fy_trend["総額_億円"]],
                )
                fig_dd.update_traces(textposition="outside")
                fig_dd.update_layout(height=320)
                st.plotly_chart(fig_dd, use_container_width=True)

            with col_stat:
                st.metric("総契約件数", f"{len(df_sel):,}件")
                st.metric("総契約金額", fmt_oku(df_sel["contract_amount"].sum()))
                zuii_v = df_sel["bid_method"].str.contains("随意", na=False).sum()
                st.metric("随意契約率", f"{zuii_v / len(df_sel) * 100:.1f}%")

            st.subheader(f"{sel_vendor} の契約一覧（{len(df_sel):,}件）")
            disp_v = (
                df_sel[["fiscal_year", "category", "contract_name", "bid_method",
                         "contract_amount", "award_rate", "contract_date"]]
                .rename(columns={
                    "fiscal_year": "年度", "category": "カテゴリ",
                    "contract_name": "件名", "bid_method": "契約方式",
                    "contract_amount": "契約金額（円）", "award_rate": "落札率",
                    "contract_date": "契約日",
                })
                .sort_values("年度", ascending=False)
            )
            st.dataframe(disp_v, use_container_width=True, height=400, hide_index=True)

        st.divider()
        col_share, col_hist = st.columns(2)

        with col_share:
            total_v = df_v["contract_amount"].sum()
            if total_v > 0 and len(vendor_agg) >= 5:
                top5_pct  = vendor_agg.head(5)["総額"].sum()  / total_v * 100
                top10_pct = vendor_agg.head(10)["総額"].sum() / total_v * 100
                st.metric("上位5社シェア",  f"{top5_pct:.1f}%")
                st.metric("上位10社シェア", f"{top10_pct:.1f}%")

        with col_hist:
            df_rate = df[df["award_rate"].notna() & (df["award_rate"] > 0) & (df["award_rate"] <= 100)]
            if not df_rate.empty:
                fig_hist = px.histogram(
                    df_rate, x="award_rate", nbins=50,
                    title="落札率分布（競争入札のみ）",
                    labels={"award_rate": "落札率（%）", "count": "件数"},
                    template=TEMPLATE,
                )
                fig_hist.update_layout(height=300)
                st.plotly_chart(fig_hist, use_container_width=True)

    # ═══════════════════════════════════════════════════════════
    # タブ3: 契約方式
    # ═══════════════════════════════════════════════════════════
    with tab3:
        col_pc, col_pm = st.columns(2)

        with col_pc:
            bid_cnt = df.groupby("bid_method").size().reset_index(name="件数")
            if not bid_cnt.empty:
                fig_bc = px.pie(
                    bid_cnt, values="件数", names="bid_method",
                    title="契約方式内訳（件数）",
                    color_discrete_sequence=["#ed7d31", "#4472c4"],
                    template=TEMPLATE,
                )
                fig_bc.update_traces(textinfo="percent+label+value")
                fig_bc.update_layout(height=360, showlegend=False)
                st.plotly_chart(fig_bc, use_container_width=True)

        with col_pm:
            bid_amt = (
                df_amt.groupby("bid_method")["contract_amount"]
                .sum()
                .reset_index()
            )
            bid_amt["金額_億円"] = bid_amt["contract_amount"] / 1e8
            if not bid_amt.empty:
                fig_bm = px.pie(
                    bid_amt, values="金額_億円", names="bid_method",
                    title="契約方式内訳（金額）",
                    color_discrete_sequence=["#ed7d31", "#4472c4"],
                    template=TEMPLATE,
                )
                fig_bm.update_traces(textinfo="percent+label")
                fig_bm.update_layout(height=360, showlegend=False)
                st.plotly_chart(fig_bm, use_container_width=True)

        st.divider()

        # 年度別 随意契約率推移
        if not df.empty:
            fy_bid = (
                df.groupby(["fiscal_year", "bid_method"])
                .size()
                .reset_index(name="件数")
            )
            fy_total = df.groupby("fiscal_year").size().reset_index(name="合計")
            fy_zuii = fy_bid[fy_bid["bid_method"].str.contains("随意", na=False)].copy()
            fy_rate = fy_zuii.merge(fy_total, on="fiscal_year")
            fy_rate["随意契約率(%)"] = fy_rate["件数"] / fy_rate["合計"] * 100
            if not fy_rate.empty:
                fig_zr = px.line(
                    fy_rate, x="fiscal_year", y="随意契約率(%)",
                    markers=True,
                    title="年度別 随意契約率推移",
                    labels={"fiscal_year": "年度"},
                    template=TEMPLATE,
                )
                fig_zr.update_layout(yaxis_range=[0, 100], height=300)
                st.plotly_chart(fig_zr, use_container_width=True)

        # 随意理由 上位
        df_zuii = df[df["zuii_reason"].notna() & (df["zuii_reason"].str.strip() != "")]
        if not df_zuii.empty:
            st.subheader("随意契約理由 上位10件")
            zr_agg = (
                df_zuii.groupby("zuii_reason")
                .agg(件数=("id", "count"), 総額_億円=("contract_amount", lambda x: x.sum() / 1e8))
                .reset_index()
                .sort_values("件数", ascending=False)
                .head(10)
                .rename(columns={"zuii_reason": "随意理由"})
            )
            st.dataframe(
                zr_agg.style.format({"総額_億円": "{:.1f}"}),
                use_container_width=True,
                hide_index=True,
            )

    # ═══════════════════════════════════════════════════════════
    # タブ4: データ閲覧
    # ═══════════════════════════════════════════════════════════
    with tab4:
        st.subheader(f"フィルター済みデータ（{len(df):,}件）")

        display_cols = [
            "fiscal_year", "category", "contract_date", "contract_name",
            "vendor_name", "bid_method", "contract_amount", "award_rate",
            "estimated_price", "zuii_reason", "source_url",
        ]
        col_rename = {
            "fiscal_year":     "年度",
            "category":        "カテゴリ",
            "contract_date":   "契約日",
            "contract_name":   "件名",
            "vendor_name":     "企業名",
            "bid_method":      "契約方式",
            "contract_amount": "契約金額（円）",
            "award_rate":      "落札率",
            "estimated_price": "予定価格（円）",
            "zuii_reason":     "随意理由",
            "source_url":      "出典URL",
        }

        disp = df[[c for c in display_cols if c in df.columns]].rename(columns=col_rename)
        st.dataframe(disp, use_container_width=True, height=500)

        col_dl1, col_dl2 = st.columns(2)

        with col_dl1:
            csv_bytes = disp.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                "📥 CSVダウンロード",
                data=csv_bytes,
                file_name="jaxa_procurement.csv",
                mime="text/csv",
            )

        with col_dl2:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                disp.to_excel(writer, index=False, sheet_name="JAXA調達")
            st.download_button(
                "📥 Excelダウンロード (.xlsx)",
                data=buf.getvalue(),
                file_name="jaxa_procurement.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
