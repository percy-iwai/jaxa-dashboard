"""
行政事業レビュー 宇宙関連ダッシュボード
データソース: data/review_space.db (build_rs_db.py 準拠スキーマ)
  - projects          事業サマリー（予算・執行額）
  - expenditure_items 歳出目別内訳
  - payees            支出先ツリー（payment-groups + payment-edges）
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "review_space.db"
TEMPLATE = "plotly_dark"

ORG_COLORS = {
    "文部科学省": "#4472c4",
    "内閣府": "#ed7d31",
    "総務省": "#70ad47",
    "経済産業省": "#ffc000",
}
DEFAULT_COLORS = ["#4472c4", "#ed7d31", "#70ad47", "#ffc000",
                  "#9e2a2b", "#8e44ad", "#0070c0", "#833c00"]


# ── データ読み込み ─────────────────────────────────────────────────

@st.cache_data(ttl=600)
def load_projects() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        """
        SELECT id, fiscal_year, name, organization, org_full_path,
               ministry_name, initial_budget, supplementary, carryover,
               budget_total, executed_amount, execution_rate, lineage_id, overview
        FROM projects
        ORDER BY ministry_name, fiscal_year, name
        """,
        conn,
    )
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_expenditure_items() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        """
        SELECT ei.*, p.name AS project_name, p.ministry_name, p.fiscal_year AS fy
        FROM expenditure_items ei
        JOIN projects p ON ei.project_id = p.id
        """,
        conn,
    )
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_payees() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        """
        SELECT py.id, py.project_id, py.fiscal_year, py.payee_name,
               py.corporate_number, py.amount, py.parent_payee_id,
               py.level, py.is_leaf, py.self_amount,
               p.name AS project_name, p.ministry_name, p.organization,
               p.budget_total AS project_budget
        FROM payees py
        JOIN projects p ON py.project_id = p.id
        ORDER BY py.amount DESC NULLS LAST
        """,
        conn,
    )
    conn.close()
    return df


# ── ヘルパー ───────────────────────────────────────────────────────

def fmt_oku(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "N/A"
    v = float(val)
    if abs(v) >= 1e8:
        return f"{v/1e8:,.1f}億円"
    if abs(v) >= 1e4:
        return f"{v/1e4:,.0f}万円"
    return f"{v:,.0f}円"


def project_color(ministry_name: str, idx: int) -> str:
    for k, c in ORG_COLORS.items():
        if k in (ministry_name or ""):
            return c
    return DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]


MAX_LEAF_NODES = 20  # サンキー図に表示する末端ノードの上限


def _parent_na(val) -> bool:
    """parent_payee_id が NULL/NaN かどうか"""
    if val is None:
        return True
    try:
        return pd.isna(val)
    except (TypeError, ValueError):
        return False


def _clean_payees(proj_payees: pd.DataFrame) -> pd.DataFrame:
    """
    - 空白・NaN 行を除去
    - "--" は「支出先（非公開 N社）」1行に集約（level=0, is_leaf=1）
    """
    df = proj_payees.copy()
    empty_mask = df["payee_name"].isna() | (df["payee_name"].str.strip() == "")
    df = df[~empty_mask]

    dash_mask = df["payee_name"].str.strip() == "--"
    dashes = df[dash_mask]
    rest = df[~dash_mask]

    if not dashes.empty:
        n = len(dashes)
        total = dashes["amount"].fillna(0).sum()
        sa_total = dashes["self_amount"].fillna(0).sum() if "self_amount" in dashes.columns else total
        consolidated = pd.DataFrame([{
            "id": -998,
            "payee_name": f"支出先（非公開 {n}社）",
            "amount": total,
            "self_amount": sa_total,
            "parent_payee_id": None,
            "level": 0,
            "is_leaf": 1,
            "corporate_number": None,
        }])
        df = pd.concat([rest, consolidated], ignore_index=True)

    return df


def build_sankey_single(
    proj: pd.Series,
    proj_payees: pd.DataFrame,
    max_leaf: int = MAX_LEAF_NODES,
) -> "go.Figure | None":
    """
    1事業分のサンキー図。
    - 正規化: scale = budget / root_sum（= sum of all self_amounts）
    - ツリーエッジ: parent→child の flow = child.amount（正規化後）
    - 途絶えポイント: 中間ノードで self_amount > 0 の場合は
      「N（直接執行）」バーチャル末端ノードを追加
    - 末端フロー合計が max_leaf 超の場合は上位 + その他 に集約
    """
    budget = float(proj.get("budget_total") or 0)
    if budget <= 0:
        return None

    ministry = proj.get("ministry_name") or "省庁不明"
    proj_name = str(proj.get("name") or "事業名不明")
    fy = int(proj.get("fiscal_year") or 0)

    df = _clean_payees(proj_payees)
    if df.empty:
        return None

    # self_amount フォールバック（古いDBへの対応）
    if "self_amount" not in df.columns:
        df["self_amount"] = df.apply(
            lambda r: r["amount"] if r.get("is_leaf", 1) == 1 else None, axis=1
        )

    # 正規化: root_sum = Σ(level-0 ノードの amount) = Σ(全ノードの self_amount)
    root_sum = df.loc[df["parent_payee_id"].apply(_parent_na), "amount"].fillna(0).sum()
    scale = budget / root_sum if root_sum > 0 else 1.0
    df["disp"] = (df["amount"].fillna(0) * scale) / 1e8
    df["self_disp"] = (df["self_amount"].fillna(0).clip(lower=0) * scale) / 1e8

    # ── 途絶えポイント（末端フロー）の列挙 ──────────────────────
    # leaf: そのノード自体が末端, virtual: 中間ノードの自己保有残額
    terminals: list[dict] = []
    for _, pay in df.iterrows():
        is_leaf = bool(pay.get("is_leaf", 1))
        sd = float(pay.get("self_disp") or 0)
        db_id = int(pay["id"]) if not pd.isna(pay.get("id")) else None
        if is_leaf:
            terminals.append({"name": str(pay["payee_name"]), "self_disp": sd,
                               "vtype": "leaf", "db_id": db_id})
        elif sd > 0.005:
            terminals.append({"name": f"{pay['payee_name']}（直接執行）", "self_disp": sd,
                               "vtype": "virtual", "db_id": db_id})

    # max_leaf 上限
    terminals.sort(key=lambda x: -x["self_disp"])
    other_row: dict | None = None
    if len(terminals) > max_leaf:
        kept = terminals[:max_leaf]
        rest = terminals[max_leaf:]
        other_disp = sum(t["self_disp"] for t in rest)
        other_row = {"name": f"その他（{len(rest)}社）", "self_disp": other_disp,
                     "vtype": "other", "db_id": None}
        terminals = kept

    kept_leaf_ids = {t["db_id"] for t in terminals if t["vtype"] == "leaf"}
    kept_virtual_ids = {t["db_id"] for t in terminals if t["vtype"] == "virtual"}

    # ── サンキー構築 ──────────────────────────────────────────────
    node_labels: list[str] = []
    node_colors: list[str] = []
    _idx: dict[str, int] = {}
    srcs: list[int] = []
    tgts: list[int] = []
    vals: list[float] = []

    def nidx(label: str, color: str = "#888888") -> int:
        if label not in _idx:
            _idx[label] = len(node_labels)
            node_labels.append(label)
            node_colors.append(color)
        return _idx[label]

    m_i = nidx(ministry, ORG_COLORS.get(ministry, "#7f7f7f"))
    p_i = nidx(proj_name, "#5b9bd5")
    srcs.append(m_i); tgts.append(p_i); vals.append(budget / 1e8)

    db_to_sankey: dict[int, int] = {}
    max_lv = int(df["level"].fillna(0).max()) if not df.empty else 0

    for lv in range(max_lv + 1):
        for _, pay in df[df["level"] == lv].iterrows():
            is_leaf = bool(pay.get("is_leaf", 1))
            db_id = int(pay["id"]) if not pd.isna(pay.get("id")) else None

            # 末端ノードはアクティブセットに含まれるものだけ描画
            if is_leaf and db_id not in kept_leaf_ids:
                continue

            color = "#27ae60" if is_leaf else "#1abc9c"
            pay_i = nidx(str(pay["payee_name"]), color)
            if db_id is not None:
                db_to_sankey[db_id] = pay_i

            parent = pay.get("parent_payee_id")
            disp = float(pay.get("disp") or 0)
            if disp <= 0:
                continue

            src_i = p_i if _parent_na(parent) else db_to_sankey.get(int(parent), p_i)
            srcs.append(src_i); tgts.append(pay_i); vals.append(disp)

            # 中間ノードの自己保有 → バーチャル末端ノード
            if not is_leaf and db_id in kept_virtual_ids:
                sd = float(pay.get("self_disp") or 0)
                if sd > 0:
                    vname = f"{pay['payee_name']}（直接執行）"
                    v_i = nidx(vname, "#e67e22")
                    srcs.append(pay_i); tgts.append(v_i); vals.append(sd)

    # その他集約ノード（事業ノードに直結）
    if other_row:
        other_i = nidx(other_row["name"], "#95a5a6")
        srcs.append(p_i); tgts.append(other_i); vals.append(other_row["self_disp"])

    if not vals:
        return None

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=15, thickness=20,
            line=dict(color="black", width=0.5),
            label=node_labels,
            color=node_colors,
        ),
        link=dict(source=srcs, target=tgts, value=vals),
    ))
    fig.update_layout(
        title=f"FY{fy} {proj_name}（予算 {budget/1e8:.0f}億円 ※正規化済）",
        template=TEMPLATE,
        height=520,
        font_size=12,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig


def build_tree_table(proj: pd.Series, proj_payees: pd.DataFrame) -> pd.DataFrame:
    """
    インデント付きツリーテーブル。
    列: 名称 / 種別 / 総受取（億円） / 自己分（億円）※予算正規化済
    途絶えポイント = 自己分 > 0 のノード（末端 or 中間ノードの残額）
    """
    budget = float(proj.get("budget_total") or 0)
    ministry = str(proj.get("ministry_name") or "省庁不明")
    proj_name = str(proj.get("name") or "事業名不明")
    fy = int(proj.get("fiscal_year") or 0)

    DASH = "—"

    rows: list[dict] = []
    rows.append({"名称": f"🏛 {ministry}", "種別": "省庁",
                 "総受取（億円）": round(budget / 1e8, 2), "自己分（億円）": DASH})
    rows.append({"名称": f"  └ 📋 FY{fy} {proj_name}", "種別": "事業",
                 "総受取（億円）": round(budget / 1e8, 2), "自己分（億円）": DASH})

    df = _clean_payees(proj_payees)
    if df.empty:
        return pd.DataFrame(rows)

    if "self_amount" not in df.columns:
        df["self_amount"] = df.apply(
            lambda r: r["amount"] if r.get("is_leaf", 1) == 1 else None, axis=1
        )

    # 正規化: root_sum = Σ(level-0 ノードの amount)
    root_sum = df.loc[df["parent_payee_id"].apply(_parent_na), "amount"].fillna(0).sum()
    scale = budget / root_sum if root_sum > 0 else 1.0
    df["disp"] = (df["amount"].fillna(0) * scale) / 1e8
    df["self_disp"] = (df["self_amount"].fillna(0).clip(lower=0) * scale) / 1e8

    # 子マップ構築
    children_map: dict[int | None, list[dict]] = {}
    for _, row in df.iterrows():
        parent = row.get("parent_payee_id")
        key: int | None = None if _parent_na(parent) else int(parent)
        children_map.setdefault(key, []).append(dict(row))
    for k in children_map:
        children_map[k].sort(key=lambda x: -(x.get("disp") or 0))

    def traverse(parent_id: int | None, indent: str) -> None:
        for i, pay in enumerate(children_map.get(parent_id, [])):
            last = (i == len(children_map[parent_id]) - 1)
            is_leaf = bool(pay.get("is_leaf", 1))
            self_d = float(pay.get("self_disp") or 0)
            prefix = "└ " if last else "├ "
            # 種別: 途絶えポイント（残額あり中間）/ 末端 / 中間
            if is_leaf:
                kind = "末端"
            elif self_d > 0.005:
                kind = "途絶ポイント"
            else:
                kind = "中間"
            icon = "" if is_leaf else "🔀 "
            rows.append({
                "名称": f"{indent}{prefix}{icon}{pay['payee_name']}",
                "種別": kind,
                "総受取（億円）": round(pay.get("disp") or 0, 2),
                "自己分（億円）": round(self_d, 2) if self_d > 0.005 else DASH,
            })
            if not is_leaf:
                new_indent = indent + ("    " if last else "│   ")
                traverse(int(pay["id"]), new_indent)

    traverse(None, "    ")
    return pd.DataFrame(rows)


# ── メイン show() ─────────────────────────────────────────────────

def show() -> None:
    if not DB_PATH.exists():
        st.info(
            "データ未取得です。以下のコマンドで取得してください:\n\n"
            "```\npython scripts/fetch_review_data.py\n```"
        )
        return

    projects = load_projects()
    exp_items = load_expenditure_items()
    payees = load_payees()

    if projects.empty:
        st.warning("projects テーブルが空です。fetch スクリプトを再実行してください。")
        return

    # ── サイドバーフィルター ──────────────────────────────────────
    with st.sidebar:
        st.header("📋 レビュー フィルター")
        all_fys = sorted(projects["fiscal_year"].dropna().unique().astype(int).tolist())
        sel_fys = st.multiselect("年度（レビュー）", all_fys, default=all_fys, key="rv_fy")
        all_min = sorted(projects["ministry_name"].dropna().unique().tolist())
        sel_min = st.multiselect("省庁（レビュー）", all_min, default=all_min, key="rv_min")

    df = projects.copy()
    if sel_fys:
        df = df[df["fiscal_year"].isin(sel_fys)]
    if sel_min:
        df = df[df["ministry_name"].isin(sel_min)]

    # ── KPI ─────────────────────────────────────────────────────
    st.title("📋 行政事業レビュー 宇宙関連事業")
    st.caption(
        "出典: 行政事業レビュー見える化サイト (rssystem.go.jp) ／ "
        "文部科学省 宇宙開発利用課・内閣府 宇宙開発戦略推進本部・総務省 宇宙通信政策課・経産省 宇宙産業課 ／ "
        f"FY{min(all_fys)}〜FY{max(all_fys)}"
    )

    df_with_budget = df[df["budget_total"].notna() & (df["budget_total"] > 0)]
    total_oku = df_with_budget["budget_total"].sum() / 1e8
    total_exec = df[df["executed_amount"].notna()]["executed_amount"].sum() / 1e8
    payee_filt = payees[payees["fiscal_year"].isin(sel_fys)] if sel_fys else payees

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("事業数", f"{len(df):,}件")
    k2.metric("予算合計", f"{total_oku:,.0f}億円")
    k3.metric("執行合計", f"{total_exec:,.0f}億円" if total_exec > 0 else "—")
    k4.metric("支出先数", f"{payee_filt['payee_name'].nunique():,}社")
    k5.metric("省庁数", f"{df['ministry_name'].nunique()}")

    st.divider()

    # ── タブ ─────────────────────────────────────────────────────
    tab_ov, tab_flow, tab_budget, tab_items, tab_data = st.tabs([
        "📌 事業一覧",
        "🔀 資金の流れ",
        "📈 予算推移",
        "📊 歳出目内訳",
        "🗂 データ一覧",
    ])

    # ═══════════════════════════════════════════════════════════
    # タブ1: 事業一覧
    # ═══════════════════════════════════════════════════════════
    with tab_ov:
        st.subheader("宇宙関連事業 一覧")

        # 省庁別件数・金額の横棒グラフ
        min_agg = (
            df_with_budget.groupby("ministry_name")
            .agg(事業数=("id", "count"), 予算合計=("budget_total", "sum"))
            .reset_index()
        )
        min_agg["予算_億円"] = min_agg["予算合計"] / 1e8
        if not min_agg.empty:
            col_bar, col_pie = st.columns(2)
            with col_bar:
                fig = px.bar(
                    min_agg, x="予算_億円", y="ministry_name",
                    orientation="h",
                    color="ministry_name",
                    color_discrete_map=ORG_COLORS,
                    labels={"予算_億円": "予算合計（億円）", "ministry_name": "省庁"},
                    title="省庁別 予算合計",
                    template=TEMPLATE,
                    text=[f"{v:.0f}億" for v in min_agg["予算_億円"]],
                )
                fig.update_traces(textposition="outside")
                fig.update_layout(showlegend=False, height=250)
                st.plotly_chart(fig, use_container_width=True)
            with col_pie:
                fig2 = px.pie(
                    min_agg, values="事業数", names="ministry_name",
                    title="省庁別 事業件数",
                    color="ministry_name",
                    color_discrete_map=ORG_COLORS,
                    template=TEMPLATE,
                )
                fig2.update_traces(textinfo="value+percent+label")
                fig2.update_layout(showlegend=False, height=250)
                st.plotly_chart(fig2, use_container_width=True)

        # 事業一覧テーブル
        st.subheader("事業一覧")
        disp = df[
            ["fiscal_year", "ministry_name", "name", "organization",
             "budget_total", "executed_amount", "execution_rate", "overview"]
        ].rename(columns={
            "fiscal_year": "年度",
            "ministry_name": "省庁",
            "name": "事業名",
            "organization": "組織",
            "budget_total": "予算額（円）",
            "executed_amount": "執行額（円）",
            "execution_rate": "執行率(%)",
            "overview": "概要",
        })
        st.dataframe(
            disp.style.format({
                "予算額（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "執行額（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "執行率(%)": lambda x: f"{x:.1f}" if x and not pd.isna(x) else "—",
            }),
            use_container_width=True, height=400, hide_index=True,
        )

        # ── 支出先 金額ランキング（末端）────────────────────────
        st.subheader("支出先 金額ランキング（末端）")
        st.info(
            "💡 **「末端」とは？**\n\n"
            "行政事業レビューの支出は省庁→交付先→委託先→再委託先…と複数段階に渡ることがあります。\n"
            "ここでは各支出先が「自分で使った金額（受取額 − 再委託額）」を集計しています。\n"
            "たとえばJAXAが1,500億円受け取り、うち1,400億円を民間企業に再委託した場合、\n"
            "JAXAの末端金額は100億円（JAXA自身が執行した分）として計上されます。\n\n"
            "**全末端金額の合計 = 事業の支出総額** になります。"
        )

        if not payees.empty and "self_amount" in payees.columns:
            pay_filt = payees[payees["fiscal_year"].isin(sel_fys)].copy() if sel_fys else payees.copy()
            pay_filt = pay_filt[pay_filt["ministry_name"].isin(sel_min)].copy() if sel_min else pay_filt
            pay_rank = (
                pay_filt[pay_filt["self_amount"].fillna(0) > 0]
                .groupby("payee_name", as_index=False)["self_amount"]
                .sum()
                .sort_values("self_amount", ascending=False)
                .head(20)
            )
            pay_rank["金額_億円"] = pay_rank["self_amount"] / 1e8
            if not pay_rank.empty:
                fig_rank = px.bar(
                    pay_rank, x="金額_億円", y="payee_name",
                    orientation="h",
                    title="支出先 末端金額 上位20社（フィルター後・正規化前）",
                    labels={"金額_億円": "末端金額合計（億円）", "payee_name": "支出先"},
                    template=TEMPLATE,
                    text=[f"{v:.1f}億" for v in pay_rank["金額_億円"]],
                )
                fig_rank.update_traces(textposition="outside", marker_color="#27ae60")
                fig_rank.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    showlegend=False,
                    height=max(350, len(pay_rank) * 28),
                )
                st.plotly_chart(fig_rank, use_container_width=True)

    # ═══════════════════════════════════════════════════════════
    # タブ2: 資金の流れ（1事業選択 + サンコー + ツリー）
    # ═══════════════════════════════════════════════════════════
    with tab_flow:
        st.subheader("資金の流れ")
        st.caption(
            "事業を1件選択するとサンキー図とインデント付きツリーを表示します。"
            "金額は**事業予算に正規化済み**（末端合計 = 事業予算）。"
        )
        st.info(
            "ℹ️ **支出先データの年度について**\n\n"
            "行政事業レビューの payment-groups は「**翌年度シートに前年度の実際の執行**」が記録される構造です。\n"
            "例: FY2023の実際の支出先 → FY2024作成シートから取得。\n\n"
            "「FY2025」事業は翌年度（FY2026）シートが未存在のため支出先データなし。"
        )

        # 予算 > 0 の事業のみ選択肢に出す
        df_sel = df[df["budget_total"].fillna(0) > 0].copy()
        if df_sel.empty:
            st.info("表示可能な事業がありません。フィルターを確認してください。")
        else:
            df_sel = df_sel.sort_values(
                ["fiscal_year", "ministry_name", "name"],
                ascending=[False, True, True],
            )
            proj_options = {
                f"FY{int(r.fiscal_year)} [{r.ministry_name}] {r['name']}": r["id"]
                for _, r in df_sel.iterrows()
            }
            sel_label = st.selectbox(
                "事業を選択", list(proj_options.keys()), key="rv_proj_sel"
            )
            sel_id = proj_options[sel_label]
            proj_row = df_sel[df_sel["id"] == sel_id].iloc[0]
            proj_pay = payees[payees["project_id"] == sel_id].copy()

            budget = float(proj_row.get("budget_total") or 0)
            # root_sum = Σ(level-0 ノードの amount) = Σ(全ノードの self_amount)
            root_sum_raw = proj_pay.loc[
                proj_pay["parent_payee_id"].isna(), "amount"
            ].fillna(0).sum()
            scale = budget / root_sum_raw if root_sum_raw > 0 else None
            # 途絶えポイント = self_amount > 0 のノード
            if "self_amount" in proj_pay.columns:
                n_terminal = int((proj_pay["self_amount"].fillna(0) > 0).sum())
            else:
                n_terminal = int((proj_pay["is_leaf"] == 1).sum())
            n_nodes = len(proj_pay)

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("事業予算", f"{budget/1e8:,.1f}億円")
            mc2.metric("支出先合計（翌年度シート）", f"{root_sum_raw/1e8:,.1f}億円" if root_sum_raw > 0 else "—",
                       help="翌年度シートの payment-groups から取得した当年度実際の支出先合計。Σ level-0 amount。")
            mc3.metric("正規化係数（予算÷支出先合計）", f"{scale:.3f}" if scale else "—",
                       help="事業予算 ÷ 支出先合計。1.0 に近いほど支出先データが予算と整合している。")
            mc4.metric("支出先ノード数", f"{n_nodes}（途絶 {n_terminal}）")

            # ── サンキー図 ──────────────────────────────────────
            fig_s = build_sankey_single(proj_row, proj_pay)
            if fig_s:
                st.plotly_chart(fig_s, use_container_width=True)
            else:
                st.info("この事業の支出先データがありません。")

            # ── インデント付きツリーテーブル ────────────────────
            tree_df = build_tree_table(proj_row, proj_pay)
            if not tree_df.empty:
                st.subheader("支出先ツリー（全節点）")
                def _style_kind(col):
                    styles = []
                    for v in col:
                        if v == "中間":
                            styles.append("font-weight:bold; color:#1abc9c")
                        elif v == "末端":
                            styles.append("color:#27ae60")
                        elif v == "途絶ポイント":
                            styles.append("font-weight:bold; color:#e67e22")
                        else:
                            styles.append("")
                    return styles
                st.dataframe(
                    tree_df.style.apply(_style_kind, subset=["種別"]),
                    use_container_width=True,
                    height=min(600, max(200, len(tree_df) * 35 + 40)),
                    hide_index=True,
                )

    # ═══════════════════════════════════════════════════════════
    # タブ3: 年度別予算推移
    # ═══════════════════════════════════════════════════════════
    with tab_budget:
        st.subheader("年度別 予算・執行額推移")

        fy_agg = (
            df.groupby(["fiscal_year", "ministry_name"])
            .agg(budget=("budget_total", "sum"), executed=("executed_amount", "sum"))
            .reset_index()
        )
        fy_agg["予算_億円"] = fy_agg["budget"] / 1e8
        fy_agg["執行_億円"] = fy_agg["executed"] / 1e8

        if not fy_agg.empty:
            fig_trend = px.bar(
                fy_agg,
                x="fiscal_year", y="予算_億円",
                color="ministry_name",
                color_discrete_map=ORG_COLORS,
                barmode="stack",
                labels={"fiscal_year": "年度", "予算_億円": "予算合計（億円）", "ministry_name": "省庁"},
                title="年度別・省庁別 予算合計",
                template=TEMPLATE,
            )
            fig_trend.update_layout(height=360, legend_title_text="省庁")
            st.plotly_chart(fig_trend, use_container_width=True)

        # 予算内訳（当初・補正・繰越）
        budget_cols = ["fiscal_year", "ministry_name", "name",
                       "initial_budget", "supplementary", "carryover",
                       "budget_total", "executed_amount", "execution_rate"]
        budget_df = df[[c for c in budget_cols if c in df.columns]].copy()
        budget_df = budget_df[budget_df["budget_total"].notna() & (budget_df["budget_total"] > 0)]

        st.subheader("予算内訳 詳細")
        pivot = (
            budget_df.rename(columns={
                "fiscal_year": "年度", "ministry_name": "省庁", "name": "事業名",
                "initial_budget": "当初予算（円）",
                "supplementary": "補正予算（円）",
                "carryover": "繰越（円）",
                "budget_total": "合計（円）",
                "executed_amount": "執行額（円）",
                "execution_rate": "執行率(%)",
            })
        )
        st.dataframe(
            pivot.style.format({
                "当初予算（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "補正予算（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "繰越（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "合計（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "執行額（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                "執行率(%)": lambda x: f"{x:.1f}" if x and not pd.isna(x) else "—",
            }),
            use_container_width=True, height=400, hide_index=True,
        )

    # ═══════════════════════════════════════════════════════════
    # タブ4: 歳出目内訳
    # ═══════════════════════════════════════════════════════════
    with tab_items:
        st.subheader("歳出目別 予算内訳")

        if exp_items.empty:
            st.info("歳出目データがありません。")
        else:
            # フィルター
            exp_filt = exp_items[exp_items["fiscal_year"].isin(sel_fys)] if sel_fys else exp_items

            if not exp_filt.empty:
                # 費目名ランキング
                purpose_agg = (
                    exp_filt[exp_filt["finalized_amount"].notna() & (exp_filt["finalized_amount"] > 0)]
                    .groupby("purpose_name")["finalized_amount"]
                    .sum()
                    .reset_index()
                    .sort_values("finalized_amount", ascending=False)
                    .head(15)
                )
                purpose_agg["金額_億円"] = purpose_agg["finalized_amount"] / 1e8

                if not purpose_agg.empty:
                    fig_pur = px.bar(
                        purpose_agg,
                        x="金額_億円", y="purpose_name",
                        orientation="h",
                        title="歳出目別 予算額 上位15項目",
                        labels={"金額_億円": "確定額（億円）", "purpose_name": "歳出目"},
                        template=TEMPLATE,
                        text=[f"{v:.1f}億" for v in purpose_agg["金額_億円"]],
                    )
                    fig_pur.update_traces(textposition="outside")
                    fig_pur.update_layout(
                        yaxis={"categoryorder": "total ascending"},
                        showlegend=False,
                        height=max(300, len(purpose_agg) * 28),
                    )
                    st.plotly_chart(fig_pur, use_container_width=True)

            st.subheader("歳出目データ一覧")
            disp_ei = exp_filt.rename(columns={
                "project_name": "事業名",
                "ministry_name": "省庁",
                "purpose_name": "歳出目",
                "upper_name": "上位費目",
                "budget_type": "予算区分",
                "requested_amount": "要求額（円）",
                "finalized_amount": "確定額（円）",
            })
            st.dataframe(
                disp_ei[["省庁", "事業名", "歳出目", "上位費目", "予算区分",
                          "要求額（円）", "確定額（円）"]].style.format({
                    "要求額（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                    "確定額（円）": lambda x: f"{x:,.0f}" if x and not pd.isna(x) else "—",
                }),
                use_container_width=True, height=400, hide_index=True,
            )

    # ═══════════════════════════════════════════════════════════
    # タブ5: データ一覧（生データ）
    # ═══════════════════════════════════════════════════════════
    with tab_data:
        st.subheader("事業マスタ")
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("支出先一覧")
        payee_disp = payees[payees["fiscal_year"].isin(sel_fys)].rename(columns={
            "fiscal_year": "年度",
            "project_name": "事業名",
            "ministry_name": "省庁",
            "payee_name": "支出先名",
            "corporate_number": "法人番号",
            "amount": "金額（円）",
        }) if sel_fys else payees
        st.dataframe(payee_disp, use_container_width=True, hide_index=True)

        st.subheader("歳出目内訳")
        st.dataframe(exp_items, use_container_width=True, hide_index=True)
