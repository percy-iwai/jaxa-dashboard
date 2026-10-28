# 指示書: 行政事業レビューのデータを API から取り、HTML 1枚のダッシュボードにする

あなた（Claude）への作業指示です。成果物は **`review_dashboard.html` 1ファイル**です。
Python・サーバー・DB は使いません（実行環境に制約があり、VM もよくリセットされるため）。
ブラウザでこのファイルを開くと、API からデータを取って集計し、表とグラフを出します。

## 0. 先に決めること（ユーザーに確認）

- **対象キーワード**: 事業名に含む語（例: `宇宙, 衛星, ロケット`）。画面上で変更できるようにもする
- **対象年度**: 既定は FY2022〜2026（API で取れる範囲）

## 1. データの在りか

出典は「行政事業レビュー見える化サイト」 https://rssystem.go.jp/ です。

- サイトの画面が内部で使っている JSON API `https://rssystem.go.jp/api/...` を使います。公式に案内されているものではありませんが、認証は不要です
- **CORS が許可されている**（`access-control-allow-origin: *`）ので、HTML の `fetch()` から直接呼べます。ローカルに置いた HTML ファイルをダブルクリックで開いても動きます
- 一括CSV（`/files/{年度}/rs/*.zip`）もありますが、シート年度 2024・2025 分しかありません。今回は使いません
- 仕様が予告なく変わる可能性があります。**リクエストの間は 300ms 以上あけて**ください
- 利用規約を確認し、出典表記を画面に必ず入れてください

## 2. API エンドポイント

| 目的 | エンドポイント | 備考 |
|---|---|---|
| 事業一覧 | `GET /api/projects/?fiscal_year=2026&page=1&page_size=100` | `{"count","next","results":[...]}`。`next` が null になるまでページを送る。1年度あたり約5,700〜6,400件（約65ページ） |
| 支出先ブロック | `GET /api/projects/{id}/payment-groups/` | 配列。支出先の金額と契約の情報 |
| ブロック間のつながり | `GET /api/projects/{id}/payment-edges/` | 配列。資金の流れ（親子関係） |

取得量の目安: 5年度分の一覧で約330回、抽出した事業（約200件）の支出先で約400回、合わせて 約730 リクエストです。
300ms 間隔で **4〜5分**かかります。

### 一覧（results の1件）で使う項目

| 項目 | 意味 |
|---|---|
| `id` | 事業の UUID（年度ごとに別の値） |
| `fiscal_year` | **シート年度** |
| `project_number` | 予算事業ID（例 `"001678"`。年度をまたいで同じ事業を追える） |
| `name` / `overview` | 事業名・概要 |
| `ministry_name`, `organization.flatten.names` | 府省名、組織の階層（府省 > 局 > 課） |
| `sheet_type` | **`RS`=レビューシート / `KS`=基金シート / `SS`=セグメント別シート** |
| `previous_year_execution_amount` | 前年度の執行額（円） |

### payment-groups の構造

```
group: id, display_code("A","B"...), name, total_amount
  └ payments[]: name, corporate_number, total_contract_amount
       └ contracts[]: amount, contract_method, number_of_applicants, bid_rate, overview
```

edges は `source_node_id → target_node_id`（どちらも group の id）です。
`source_node_id` が null のものは、**府省から直接支出されたブロック（ルート）**です。

`contract_method` のコードは日本語ラベルに直します:

```js
const METHOD = {
  "subsidy":"補助金等交付", "management-expense-grant":"運営費交付金交付",
  "act-bearing-national-treasury-liabilities":"国庫債務負担行為等",
  "open-tendering-lowest-price":"一般競争契約（最低価格）",
  "open-tendering-comprehensive-evaluation":"一般競争契約（総合評価）",
  "selective-tendering-lowest-price":"指名競争契約（最低価格）",
  "selective-tendering-comprehensive-evaluation":"指名競争契約（総合評価）",
  "negotiated-contract-small-amount":"随意契約（少額）",
  "negotiated-contract-competitive-bidding":"随意契約（企画競争）",
  "negotiated-contract-public-offering":"随意契約（公募）",
  "negotiated-contract-unsuccessful":"随意契約（不落・不調）",
  "negotiated-contract-others":"随意契約（その他）", "others":"その他",
};
```

## 3. 必ず守る注意点（ハマりどころ）

1. **`sheet_type === "RS"` だけに絞る。** `KS`（基金）と `SS`（セグメント別）は同じお金を別シートに重ねて載せていることがあり、合算すると二重計上になります
2. **年度のずれ。** シート年度 N の支出先は、**N−1 年度に実際に払った実績**です。年度はシート年度のまま表示し、画面に「支出先は各シート年度の前年度実績」と注記してください
3. **FY2022・2023 は旧形式。** ブロック名が「--」で、payment-edges が空です。再委託先も最上位に並ぶので、ブロックの金額を足すと二重計上になります。edges が空でブロックが2つ以上ある事業は、事業の合計に `previous_year_execution_amount` を使ってください
4. **金額の単位は円**です。表示は `(v/1e8).toFixed(1) + "億円"` に統一します
5. 旧形式では、同じ支出先名が同じブロックに何行も出ます（契約ごとに並ぶため）。誤りではありません

## 4. データの持ち方（DB の代わり）

取得したデータは次の形の JSON にまとめ、メモリに置きます。この JSON が DB の代わりです。

```js
{
  "fetched_at": "2026-10-28T21:00:00",
  "keywords": ["宇宙","衛星","ロケット"],
  "projects": [ { "key":"2026-1678", "fy":2026, "project_number":1678, "name":"...",
                  "ministry":"文部科学省", "org_path":"文部科学省 > 研究開発局 > ...",
                  "executed":5526907000, "old_format":false } ],
  "payees": [ { "id":1, "project_key":"2026-1678", "fy":2026, "block":"A", "name":"...",
                "corporate_number":"...", "amount":0, "parent_id":null, "level":0,
                "is_leaf":1, "self_amount":0, "summary":"...", "method":"...",
                "bids":"...", "rate":"..." } ]
}
```

支出先ツリーの作り方（JS で実装）:

- edges からルートと親子関係を作る。edges が空ならブロックを全部ルートとして扱う
- 子を持つブロック → ブロック名で1行（`is_leaf=0`）
- 末端のブロック → `payments` を1件ずつ1行にする（`is_leaf=1`）。概要・契約方式・入札者数・落札率は `contracts` から重複を除いて連結する
- `self_amount = amount − 直接の子の amount の合計`（0未満は0）。「その支出先が自分で使った額」の意味
- 事業の `executed` = ルート行の amount 合計（旧形式の例外は注意点3のとおり）

**VM がリセットされても困らないようにする:**

- 「JSON を保存」ボタン: 上の JSON をファイルとしてダウンロードさせる（`Blob` ＋ `a[download]`）
- 「JSON を読み込む」ボタン: `<input type="file">` で読み込んで、取得し直さずに表示する
- 取得の途中経過は IndexedDB にキャッシュする（任意）。localStorage は容量が 5MB 程度なので使わない

## 5. 画面の構成（`review_dashboard.html`）

外部ライブラリに頼らない作りにします。CDN が使えない環境を想定し、グラフは **素の SVG か CSS の横棒**で描きます。

- **上部**: タイトル、出典表記、年度の注記（注意点2）、データ取得日時
- **操作パネル**: キーワード入力、年度チェックボックス、「API から取得」「JSON を保存」「JSON を読み込む」ボタン、進捗バー（「一覧 FY2024 12/63ページ」「支出先 87/196事業」）
- **フィルタ**: 年度、府省（複数選択）、事業名の検索
- **KPI 行**: 事業数 / 執行額合計（億円）/ 支出先数（法人番号のユニーク数）/ 府省数
- **タブ1 事業一覧**: 府省別の執行額（横棒）＋ 事業の表。列見出しクリックで並べ替え
- **タブ2 年度推移**: 年度 × 府省の積み上げ棒（SVG）
- **タブ3 支出先ランキング**: `is_leaf=1` の `self_amount` を支出先名で合計した上位20社（横棒）。行をクリックすると事業別の内訳を表示
- **タブ4 資金の流れ**: 事業を選ぶと、支出先ツリーを字下げした表で表示（府省 → ブロック → 支出先。金額付き。`<details>` で開閉）
- **CSV ダウンロード**: 事業表・支出先表それぞれ。Excel で文字化けしないよう **BOM（`﻿`）付き UTF-8** で出す

実装のコツ:

- 取得処理は `async` 関数で順番に `await fetch()` し、間に `await sleep(300)` を入れる。失敗したら3回まで待ち時間を延ばして再試行する
- 一覧は全件を取ってから、事業名とキーワードで絞る（`organization_ids` で絞るより確実）
- 表示用の文字列と、計算・CSV 用の数値を分けて持つ
- 1ファイルに HTML・CSS・JS をすべて入れる。日本語フォントは `system-ui` 系で十分

## 6. 環境で API に届かない場合

組織のプロキシなどで、ブラウザから rssystem.go.jp に届かない環境もあります。その場合は次のようにしてください。

1. 外につながる別の PC で同じ HTML を開き、「API から取得」→「JSON を保存」する
2. その JSON を制約環境に持ち込み、「JSON を読み込む」で表示する

HTML 側はどちらのやり方でも同じように表示できるようにしておいてください。

## 7. 完了条件

- [ ] HTML をブラウザで開き、「API から取得」で5年度分が4〜5分で取れて、進捗が見える
- [ ] 年度別の事業数・執行額合計の表が出る。新形式（FY2024 以降）は、ルート合計と `previous_year_execution_amount` の合計の差が1%程度に収まる
- [ ] 「JSON を保存」→ ページを再読み込み →「JSON を読み込む」で、通信せずに同じ画面に戻る
- [ ] 4タブすべてが表示され、フィルタが効く
- [ ] CSV を Excel で開いても文字化けしない
- [ ] 外部ライブラリ・CDN を使っていない（インターネットにつながらない環境でも JSON 読み込みで動く）
