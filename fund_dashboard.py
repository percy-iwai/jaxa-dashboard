"""
宇宙戦略基金ダッシュボード  v2026-05-15-2
データソース: data/fund_jaxa.db
  - themes        テーマ一覧（省庁・期別・分野）
  - theme_budgets 予算情報（支援総額・採択予定件数）
  - organizations 採択機関
  - research_tasks 研究課題
"""

import io
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "fund_jaxa.db"
TEMPLATE = "plotly_dark"

# ── 省庁カラー ─────────────────────────────────────────────────────
ORG_COLORS = {
    "文部科学省": "#4472c4",
    "内閣府":     "#ed7d31",
    "総務省":     "#70ad47",
    "経済産業省": "#ffc000",
}
DEFAULT_COLORS = [
    "#4472c4", "#ed7d31", "#70ad47", "#ffc000",
    "#9e2a2b", "#8e44ad", "#0070c0", "#833c00",
]

# ── カテゴリ定義（fund.jaxa.jp 公式4分類） ────────────────────────
CATEGORY_ORDER = ["宇宙輸送", "衛星等", "探査等", "分野共通"]
CATEGORY_COLORS: dict[str, str] = {
    "宇宙輸送": "#e74c3c",
    "衛星等":   "#3498db",
    "探査等":   "#9b59b6",
    "分野共通": "#f39c12",
}
PERIOD_ORDER = ["第一期", "第二期", "第三期"]

# ── 団体名正規化 ───────────────────────────────────────────────────
# org_type の誤記をダッシュボード側で補正（DB修正が来たら削除可）
_ORG_TYPE_ALIASES: dict[str, str] = {
    "株式会社QPS研究所":  "民間企業",
    "学校法人立命館":    "大学",
    "三菱重工業株式会社": "民間企業",
}

_GARBAGE = {"スペース", "株式会社"}
_ALIASES = {
    "NECスペーステクノロジー":              "NECスペーステクノロジー株式会社",
    "NU-Rei":                              "NU-Rei株式会社",
    "インターステラテクノロジズ":            "インターステラテクノロジズ株式会社",
    "コンポジットテーラーズ":                "コンポジットテーラーズ株式会社",
    "シャープエネルギーソリューション":      "シャープエネルギーソリューション株式会社",
    "三菱プレシジョン":                      "三菱プレシジョン株式会社",
    "三菱重工業":                            "三菱重工業株式会社",
    "三菱電機":                              "三菱電機株式会社",
    "丸八":                                  "丸八株式会社",
    "日本郵船":                              "日本郵船株式会社",
    "日本電気":                              "日本電気株式会社",
    "Space BD 株式会社":                    "Space BD株式会社",
    "株式会社 Preferred Networks":          "株式会社Preferred Networks",
    "株式会社 ジーエス・ユアサ テクノロジー": "株式会社ジーエス・ユアサ テクノロジー",
    "東京大学 大学院":                       "国立大学法人東京大学",
    "国立大学法人東海国立大学機構 名古屋大学": "国立大学法人東海国立大学機構名古屋大学",
    "東海国立大学機構名古屋大学":            "国立大学法人東海国立大学機構名古屋大学",
    "大学共同利用機関法人自然科学研究機構":  "大学共同利用機関法人自然科学研究機構国立天文台",
    "国立天文台":                            "大学共同利用機関法人自然科学研究機構国立天文台",
    "株式会社アークエッジ":                  "株式会社アークエッジ・スペース",
}


def _normalize_org(name) -> str | None:
    if not name or not isinstance(name, str):
        return None
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r"  +", " ", name)
    if name in _GARBAGE:
        return None
    return _ALIASES.get(name, name)


def fmt_oku(val) -> str:
    if pd.isna(val) or val is None:
        return "—"
    val = float(val)
    if val >= 100:
        return f"{val:.0f}億円"
    if val >= 1:
        return f"{val:.1f}億円"
    return f"{val * 100:.0f}百万円"


def _cat_order_key(x: str) -> int:
    try:
        return CATEGORY_ORDER.index(x)
    except ValueError:
        return 99


# ── データ読み込み ─────────────────────────────────────────────────

@st.cache_data(ttl=600)
def load_themes() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    # 将来追加予定の任意カラムを確認
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(themes)")
    existing_cols = {row[1] for row in cur.fetchall()}
    _opt = ["overview_pdf_url", "overview_pdf_page", "result_pdf_url", "result_pdf_page"]
    extra = "".join(f",\n            t.{c}" for c in _opt if c in existing_cols)
    df = pd.read_sql_query(
        f"""
        SELECT
            t.theme_id, t.theme_name, t.period, t.domain,
            t.ministry, t.adoption_date,
            t.detail_url, t.pr_sheet_url, t.pr_sheet_page{extra},
            SUM(tb.total_budget_oku) AS total_budget_oku,
            SUM(tb.expected_cases)   AS expected_cases,
            (SELECT COUNT(*) FROM organizations o WHERE o.theme_id = t.theme_id) AS adopted_count
        FROM themes t
        LEFT JOIN theme_budgets tb ON t.theme_id = tb.theme_id
        GROUP BY t.theme_id
        ORDER BY t.ministry, t.period, t.theme_name
        """,
        conn,
    )
    conn.close()
    # 任意カラムが DB になければ空列として追加（表示コードが列存在を前提とするため）
    for c in _opt:
        if c not in df.columns:
            df[c] = None
    # domain は DB で既に4分類に正規化済み
    df["category"] = df["domain"]
    return df


@st.cache_data(ttl=600)
def load_organizations() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        """
        SELECT o.id, o.theme_id, t.theme_name, t.ministry, t.period,
               o.org_name, o.is_lead, o.org_type
        FROM organizations o
        JOIN themes t ON o.theme_id = t.theme_id
        ORDER BY t.ministry, t.period, t.theme_name, o.org_name
        """,
        conn,
    )
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_research_tasks() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(research_tasks)")
    cols = {row[1] for row in cur.fetchall()}
    # 開始・終了日カラム: DB更新で start_date/end_date が入る予定。
    # 現行DBの period_start/period_end にもフォールバックし、どちらも無ければ NULL。
    start_col = "start_date" if "start_date" in cols else (
        "period_start" if "period_start" in cols else None)
    end_col = "end_date" if "end_date" in cols else (
        "period_end" if "period_end" in cols else None)
    start_expr = f"r.{start_col}" if start_col else "NULL"
    end_expr   = f"r.{end_col}" if end_col else "NULL"
    df = pd.read_sql_query(
        f"""
        SELECT r.id, r.theme_id, t.theme_name, t.ministry, t.period,
               r.org_name, r.task_name, r.task_overview,
               r.committee_type, r.subsidy_rate,
               {start_expr} AS start_date,
               {end_expr}   AS end_date
        FROM research_tasks r
        JOIN themes t ON r.theme_id = t.theme_id
        ORDER BY t.ministry, t.period, t.theme_name, r.org_name
        """,
        conn,
    )
    conn.close()
    return df


@st.cache_data(ttl=600)
def _has_org_sub_theme() -> bool:
    """organizations テーブルに sub_theme カラムがあるか（按分方式の切替に使用）。"""
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(organizations)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    return "sub_theme" in cols


@st.cache_data(ttl=600)
def load_org_summary() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    has_sub_theme = _has_org_sub_theme()
    conn = sqlite3.connect(str(DB_PATH))
    org_extra = ", o.sub_theme" if has_sub_theme else ""
    df_o = pd.read_sql_query(
        f"""
        SELECT o.id, o.theme_id, o.org_name, o.org_type{org_extra},
               t.theme_name, t.ministry, t.period
        FROM organizations o
        JOIN themes t ON o.theme_id = t.theme_id
        ORDER BY o.org_name, t.period, t.theme_name
        """,
        conn,
    )
    df_b = pd.read_sql_query(
        "SELECT theme_id, sub_theme, total_budget_oku FROM theme_budgets",
        conn,
    )
    conn.close()

    if not has_sub_theme:
        df_o["sub_theme"] = None

    # 按分の分子（支援総額）・分母（採択機関数）をテーマ単位／サブテーマ単位で用意
    theme_budget = df_b.groupby("theme_id")["total_budget_oku"].sum(min_count=1)
    theme_count  = df_o.groupby("theme_id")["id"].count()
    sub_budget = (
        df_b.dropna(subset=["sub_theme"])
        .groupby(["theme_id", "sub_theme"])["total_budget_oku"].sum(min_count=1)
    )
    sub_count = (
        df_o.dropna(subset=["sub_theme"])
        .groupby(["theme_id", "sub_theme"])["id"].count()
    )

    # 1行ずつ按分: organizations.sub_theme が theme_budgets の sub_theme と一致し
    # かつそのサブテーマ予算が判明している場合のみサブテーマ別按分。
    # 一致しない・予算NULL・複合値(例 "A,B")・sub_theme未割当はテーマ全体で按分。
    records = []
    for row in df_o.itertuples(index=False):
        tid, sub = row.theme_id, row.sub_theme
        sub_bud = (
            sub_budget.get((tid, sub))
            if has_sub_theme and isinstance(sub, str) and sub
            else None
        )
        if sub_bud is not None and pd.notna(sub_bud):
            cnt = int(sub_count.get((tid, sub), 0))
            bud = sub_bud
        else:
            cnt = int(theme_count.get(tid, 0))
            bud = theme_budget.get(tid)
        est = (bud / cnt) if (cnt and pd.notna(bud)) else None
        records.append({
            "org_count": cnt,
            "theme_total_oku": bud,
            "estimated_budget_oku": est,
        })
    df = pd.concat([df_o.reset_index(drop=True), pd.DataFrame(records)], axis=1)

    # 正規化前の表記違いによる重複を安全ネットとして排除
    df["org_name"] = df["org_name"].map(_normalize_org)
    df = df[df["org_name"].notna()]
    df = df.drop_duplicates(subset=["org_name", "theme_id", "sub_theme"])
    # org_type の誤記補正
    for name, otype in _ORG_TYPE_ALIASES.items():
        df.loc[df["org_name"] == name, "org_type"] = otype
    return df.reset_index(drop=True)



def _show_org_detail(org_name: str, scope_summary: pd.DataFrame) -> None:
    rows = scope_summary[scope_summary["org_name"] == org_name].copy()
    if rows.empty:
        st.info("詳細データがありません。")
        return
    rows["支援総額（按分元）"] = rows["theme_total_oku"].apply(fmt_oku)
    rows["推計採択額"]        = rows["estimated_budget_oku"].apply(fmt_oku)
    total = rows["estimated_budget_oku"].sum()
    st.markdown(f"**{org_name}** の採択テーマ一覧")
    st.markdown(f"合計推計採択額: **{fmt_oku(total)}**（按分推計）")
    cols = ["theme_name", "ministry", "period"]
    rename = {
        "theme_name": "テーマ名",
        "ministry":   "省庁",
        "period":     "期",
        "org_count":  "按分機関数",
    }
    # sub_theme 別按分が効いている場合のみサブテーマ列を表示
    if "sub_theme" in rows.columns and rows["sub_theme"].notna().any():
        cols.append("sub_theme")
        rename["sub_theme"] = "サブテーマ"
    cols += ["支援総額（按分元）", "org_count", "推計採択額"]
    st.dataframe(
        rows[cols].rename(columns=rename),
        use_container_width=True,
        hide_index=True,
    )


# ── メイン表示 ────────────────────────────────────────────────────

def show():
    st.header("💰 宇宙戦略基金")

    if not DB_PATH.exists():
        st.error(f"データベースが見つかりません: {DB_PATH}")
        return

    df_themes = load_themes()
    df_tasks  = load_research_tasks()

    df_orgs = load_organizations().copy()
    df_orgs["org_name"] = df_orgs["org_name"].map(_normalize_org)
    df_orgs = df_orgs[df_orgs["org_name"].notna()].reset_index(drop=True)
    for _name, _otype in _ORG_TYPE_ALIASES.items():
        df_orgs.loc[df_orgs["org_name"] == _name, "org_type"] = _otype

    df_org_summary = load_org_summary()

    if df_themes.empty:
        st.warning("テーマデータがありません。")
        return

    # ── フィルタ（省庁 / 期 / 大カテゴリ） ──────────────────
    col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
    with col_f1:
        ministries = ["全省庁"] + sorted(df_themes["ministry"].dropna().unique().tolist())
        sel_ministry = st.selectbox("省庁", ministries, key="fund_ministry")

    with col_f2:
        periods = [p for p in PERIOD_ORDER if p in df_themes["period"].unique()]
        sel_periods = st.multiselect("期", periods, default=periods, key="fund_period")

    with col_f3:
        all_cats = sorted(df_themes["category"].unique().tolist(), key=_cat_order_key)
        sel_categories = st.multiselect("大カテゴリ", all_cats, default=all_cats, key="fund_category")

    df_filtered = df_themes.copy()
    if sel_ministry != "全省庁":
        df_filtered = df_filtered[df_filtered["ministry"] == sel_ministry]
    if sel_periods:
        df_filtered = df_filtered[df_filtered["period"].isin(sel_periods)]
    if sel_categories:
        df_filtered = df_filtered[df_filtered["category"].isin(sel_categories)]

    theme_ids = df_filtered["theme_id"].tolist()

    # 期別サマリー用（省庁・カテゴリのみ適用、期フィルタ外す）
    df_for_period = df_themes.copy()
    if sel_ministry != "全省庁":
        df_for_period = df_for_period[df_for_period["ministry"] == sel_ministry]
    if sel_categories:
        df_for_period = df_for_period[df_for_period["category"].isin(sel_categories)]

    # ── 期別サマリー ─────────────────────────────────────────
    st.subheader("期別サマリー")
    period_cols = st.columns(len(PERIOD_ORDER))
    for i, period in enumerate(PERIOD_ORDER):
        pf = df_for_period[df_for_period["period"] == period]
        period_theme_ids = pf["theme_id"].tolist()
        period_orgs = df_orgs[df_orgs["theme_id"].isin(period_theme_ids)]
        n_themes = len(pf)
        budget   = pf["total_budget_oku"].sum()
        n_orgs   = period_orgs["org_name"].nunique()
        with period_cols[i]:
            st.metric(period, f"{n_themes}テーマ")
            st.metric("支援総額",   fmt_oku(budget) if n_themes else "—")
            st.metric("採択機関数", f"{n_orgs}機関" if n_themes else "—")

    # ── テーマ一覧 ────────────────────────────────────────────
    st.subheader(f"テーマ一覧（{len(df_filtered)}件）")
    display = df_filtered.copy()
    display["支援総額"] = display["total_budget_oku"].apply(fmt_oku)

    # 採択件数：第一期・第二期は実績（organizations登録数）、第三期は予定
    adopted_strs = []
    for _, _r in display.iterrows():
        if _r.get("period") in ("第一期", "第二期"):
            cnt = _r.get("adopted_count", 0) or 0
            adopted_strs.append(f"{int(cnt)}件" if cnt else "—")
        else:
            v = _r.get("expected_cases")
            adopted_strs.append(f"{int(v)}件" if pd.notna(v) and v else "—")
    display["採択件数"] = adopted_strs

    def _make_pdf_links(url_col, page_col, period_col=None, periods_shown=None):
        urls  = display[url_col].fillna("").astype(str).str.strip()
        pages = display[page_col].fillna("").astype(str)
        result = []
        for u, p, per in zip(urls, pages, display[period_col] if period_col else [""] * len(display)):
            if periods_shown and per not in periods_shown:
                result.append("")
                continue
            u = u if u and u != "nan" else ""
            p = p if p and p != "nan" else ""
            if not u:
                result.append("")
            elif p:
                result.append(f"{u}#page={int(float(p))}")
            else:
                result.append(u)
        return result

    display["PRシート"] = _make_pdf_links("pr_sheet_url", "pr_sheet_page")
    display["案件概要"] = _make_pdf_links("overview_pdf_url", "overview_pdf_page")
    display["採択結果"] = _make_pdf_links("result_pdf_url", "result_pdf_page",
                                          period_col="period", periods_shown={"第一期", "第二期"})

    show_cols = ["theme_name", "ministry", "period", "category", "支援総額", "採択件数", "PRシート"]
    col_cfg: dict = {"PRシート": st.column_config.LinkColumn("PRシート", display_text="PDF")}
    if display["案件概要"].any():
        show_cols.append("案件概要")
        col_cfg["案件概要"] = st.column_config.LinkColumn("案件概要PDF", display_text="PDF")
    if display["採択結果"].any():
        show_cols.append("採択結果")
        col_cfg["採択結果"] = st.column_config.LinkColumn("採択結果PDF", display_text="PDF")

    st.dataframe(
        display[show_cols].rename(columns={
            "theme_name": "テーマ名",
            "ministry":   "省庁",
            "period":     "期",
            "category":   "大カテゴリ",
        }),
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
    )
    st.caption(
        "※採択件数：第一期・第二期は採択実績、第三期は採択予定（推計含む）。"
        "支援規模に幅がある場合、支援上限額・採択件数下限値を表示し raw_text に原文を記録しています。"
    )
    _buf_th = io.BytesIO()
    with pd.ExcelWriter(_buf_th, engine="openpyxl") as _w:
        display[["theme_name", "ministry", "period", "category",
                 "total_budget_oku", "採択件数"]].rename(columns={
            "theme_name": "テーマ名", "ministry": "省庁", "period": "期",
            "category": "大カテゴリ", "total_budget_oku": "支援総額（億円）",
        }).to_excel(_w, index=False, sheet_name="テーマ一覧")
    st.download_button("📥 テーマ一覧をExcelダウンロード", data=_buf_th.getvalue(),
                       file_name="jaxa_fund_themes.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ── 支援総額分析 ──────────────────────────────────────────
    st.subheader("支援総額分析")
    tab_theme_rank, tab_cat_period = st.tabs(["テーマ別ランキング", "カテゴリ×期別"])

    with tab_theme_rank:
        rank_df = df_filtered[df_filtered["total_budget_oku"].notna()].sort_values(
            "total_budget_oku", ascending=True
        )
        if rank_df.empty:
            st.info("予算データのあるテーマがフィルタ条件に該当しません。")
        else:
            fig = px.bar(
                rank_df,
                x="total_budget_oku", y="theme_name",
                color="ministry",
                color_discrete_map=ORG_COLORS,
                orientation="h",
                labels={"total_budget_oku": "支援総額（億円）", "theme_name": "テーマ", "ministry": "省庁"},
                template=TEMPLATE,
                height=max(300, len(rank_df) * 28),
            )
            fig.update_layout(
                yaxis={"categoryorder": "total ascending"},
                margin={"l": 0, "r": 20, "t": 20, "b": 40},
                legend_title_text="省庁",
            )
            st.plotly_chart(fig, use_container_width=True)

    with tab_cat_period:
        cat_df = df_for_period[df_for_period["total_budget_oku"].notna()].copy()
        if cat_df.empty:
            st.info("予算データがありません。")
        else:
            cat_period = (
                cat_df.groupby(["category", "period"], dropna=False)
                .agg(支援総額=("total_budget_oku", "sum"), テーマ数=("theme_id", "count"))
                .reset_index()
            )
            fig_cp = px.bar(
                cat_period,
                x="category", y="支援総額",
                color="period",
                barmode="group",
                category_orders={"category": CATEGORY_ORDER, "period": PERIOD_ORDER},
                color_discrete_sequence=["#5b8dee", "#f5a623", "#7ed321"],
                labels={"category": "大カテゴリ", "支援総額": "支援総額（億円）", "period": "期"},
                template=TEMPLATE,
                text_auto=".0f",
                title="大カテゴリ×期別 支援総額",
            )
            fig_cp.update_layout(
                margin={"l": 0, "r": 20, "t": 40, "b": 40},
                legend_title_text="期",
            )
            fig_cp.update_traces(textposition="outside", textfont_size=10)
            st.plotly_chart(fig_cp, use_container_width=True)

            cat_summary = (
                cat_df.groupby("category")
                .agg(テーマ数=("theme_id", "count"), 支援総額_億円=("total_budget_oku", "sum"))
                .reset_index()
                .sort_values("支援総額_億円", ascending=False)
            )
            cat_summary["支援総額"] = cat_summary["支援総額_億円"].apply(fmt_oku)
            st.dataframe(
                cat_summary[["category", "テーマ数", "支援総額"]].rename(columns={"category": "大カテゴリ"}),
                use_container_width=True,
                hide_index=True,
            )

    # ── 採択機関・研究課題 ────────────────────────────────────
    st.subheader("採択機関・研究課題")
    tab_org_agg, tab_tasks, tab_orgs = st.tabs(["機関別集計（推計）", "研究課題", "採択機関一覧"])

    with tab_org_agg:
        st.info(
            "⚠️ **推計値について**: 採択金額は非公開のため、"
            "「テーマの支援総額 ÷ そのテーマの採択機関数」で按分した推計値です。"
            "実際の採択額とは異なります。",
        )
        if _has_org_sub_theme():
            st.caption(
                "※推計採択額 = 各サブテーマの支援総額 ÷ 同サブテーマ内採択機関数 の合計"
                "（organizations.sub_theme に基づくサブテーマ別按分）。"
                "サブテーマ未割当・サブテーマ予算が非公開・複数サブテーマ参加の機関は"
                "テーマ全体で按分。"
            )
        else:
            st.caption(
                "※推計採択額 = 各テーマの支援総額 ÷ 同テーマ内採択機関数 の合計。"
                "organizationsテーブルにsub_themeカラムがないため、"
                "サブテーマ（A/B/C）別の按分には非対応。"
            )
        scope_summary = df_org_summary[df_org_summary["theme_id"].isin(theme_ids)].copy()
        if scope_summary.empty:
            st.info("集計データがありません。")
        else:
            agg = (
                scope_summary.groupby(["org_name", "org_type"], dropna=False)
                .agg(
                    テーマ数=("theme_id", "nunique"),
                    参加件数=("theme_id", "count"),
                    推計採択額_億円=("estimated_budget_oku", "sum"),
                )
                .reset_index()
                .rename(columns={"org_name": "組織名", "org_type": "機関種別"})
                .sort_values("推計採択額_億円", ascending=False, na_position="last")
            )

            top20 = agg[agg["推計採択額_億円"].notna()].head(20)
            if not top20.empty:
                fig_org = px.bar(
                    top20.sort_values("推計採択額_億円", ascending=True),
                    x="推計採択額_億円", y="組織名",
                    color="機関種別",
                    orientation="h",
                    labels={"推計採択額_億円": "推計採択額（億円・按分）"},
                    template=TEMPLATE,
                    height=max(300, len(top20) * 26),
                    title="推計採択額ランキング（上位20機関）※按分推計",
                )
                fig_org.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    margin={"l": 0, "r": 20, "t": 40, "b": 40},
                )
                st.plotly_chart(fig_org, use_container_width=True)

            org_list = agg["組織名"].tolist()
            sel_org = st.selectbox(
                "企業・機関を選択してテーマ別明細を表示",
                ["（選択してください）"] + org_list,
                key="fund_org_detail",
            )
            if sel_org != "（選択してください）":
                _show_org_detail(sel_org, scope_summary)

            st.markdown("---")
            display_agg = agg.copy()
            display_agg["推計採択額"] = display_agg["推計採択額_億円"].apply(fmt_oku)
            st.dataframe(
                display_agg[["組織名", "機関種別", "テーマ数", "参加件数", "推計採択額"]],
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"{len(agg)}機関")
            _buf_og = io.BytesIO()
            with pd.ExcelWriter(_buf_og, engine="openpyxl") as _w:
                display_agg[["組織名", "機関種別", "テーマ数", "推計採択額_億円"]].rename(
                    columns={"推計採択額_億円": "推計採択額（億円）"}
                ).to_excel(_w, index=False, sheet_name="採択機関集計")
            st.download_button(
                "📥 採択機関一覧をExcelダウンロード", data=_buf_og.getvalue(),
                file_name="jaxa_fund_orgs.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with tab_tasks:
        st.caption("※開始日・終了日はDBに収録された値を表示（未収録の課題は空欄）")
        tasks_in_scope = df_tasks[df_tasks["theme_id"].isin(theme_ids)]
        if tasks_in_scope.empty:
            st.info("研究課題データがありません。")
        else:
            theme_options = ["全テーマ"] + df_filtered["theme_name"].tolist()
            sel_theme = st.selectbox("テーマで絞り込み", theme_options, key="fund_theme_sel")
            if sel_theme != "全テーマ":
                tasks_in_scope = tasks_in_scope[tasks_in_scope["theme_name"] == sel_theme]
            disp_tasks = tasks_in_scope.copy()
            disp_tasks["start_date"] = disp_tasks["start_date"].fillna("")
            disp_tasks["end_date"]   = disp_tasks["end_date"].fillna("")
            st.dataframe(
                disp_tasks[[
                    "theme_name", "org_name", "task_name",
                    "start_date", "end_date", "task_overview",
                ]].rename(columns={
                    "theme_name":    "テーマ",
                    "org_name":      "組織名",
                    "task_name":     "課題名",
                    "start_date":    "開始日",
                    "end_date":      "終了日",
                    "task_overview": "概要",
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"{len(tasks_in_scope)}件")
            _buf_tk = io.BytesIO()
            with pd.ExcelWriter(_buf_tk, engine="openpyxl") as _w:
                disp_tasks[["theme_name", "org_name", "task_name",
                            "start_date", "end_date", "task_overview"]].rename(
                    columns={"theme_name": "テーマ名", "org_name": "機関名",
                             "task_name": "課題名", "start_date": "開始日",
                             "end_date": "終了日", "task_overview": "概要"}
                ).to_excel(_w, index=False, sheet_name="研究課題")
            st.download_button(
                "📥 研究課題一覧をExcelダウンロード", data=_buf_tk.getvalue(),
                file_name="jaxa_fund_tasks.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with tab_orgs:
        orgs_in_scope = df_orgs[df_orgs["theme_id"].isin(theme_ids)]
        if orgs_in_scope.empty:
            st.info("採択機関データがありません。")
        else:
            type_summary = (
                orgs_in_scope.groupby("org_type", dropna=False)
                .size().reset_index(name="件数")
                .sort_values("件数", ascending=False)
            )
            st.dataframe(type_summary, use_container_width=True, hide_index=True)
            st.markdown("---")
            st.dataframe(
                orgs_in_scope[["period", "theme_name", "org_name", "org_type"]].rename(columns={
                    "period":     "期",
                    "theme_name": "テーマ",
                    "org_name":   "組織名",
                    "org_type":   "機関種別",
                }),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"{len(orgs_in_scope)}件")
