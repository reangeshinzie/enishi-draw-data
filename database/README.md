# 過去抽せん結果データベース

目的は端末内での過去番号照合。課金判定、番号生成、画面、現行の
`public/draws.json` と自動配信ワークフローはこの作業では変更しない。

## 成果物

- `draw-history.sqlite3`: SQLite、`PRAGMA user_version = 1`。
- `manifest.json`: ゲーム別収録範囲・件数・欠番検査結果・容量・SHA-256。
- 対象: miniloto / loto6 / loto7 / numbers3 / numbers4 / bingo5。
- 回号、日付、本数字、ボーナス数字または桁文字列を保持する。
- 賞金・口数は収録しない。過去照合の「等級相当」と実際の購入・当選を区別する。

## テーブル

`draws` の主キーは `(game, draw_number)`。`draw_date` は ISO 8601 の日付。

| フィールド | ロト系 | ナンバーズ | ビンゴ5 |
|---|---|---|---|
| main_numbers | 昇順の整数JSON配列 | NULL | 中央FREEを除く8マスの整数JSON配列 |
| bonus_numbers | 整数JSON配列 | NULL | 空配列 |
| digits | NULL | 桁数固定の文字列（0097など） | NULL |

ビンゴ5の配列は左上から行順で、中央FREEを除く。インデックス0〜7は
それぞれ1〜5、6〜10、…、36〜40に対応。数字を任意の順番へ並べ替えない。
ナンバーズは重複する数字が有効。数値型への変換や並べ替えをしない。

`sources` は参照URL、UTC取得日時、ローカル取得スナップショットのSHA-256。
`draw_sources` で各結果と出典を関連づける。`metadata` に収録範囲を格納する。

```sql
SELECT draw_number, draw_date, main_numbers, bonus_numbers
FROM draws WHERE game = 'loto6' ORDER BY draw_number;

SELECT draw_number, draw_date FROM draws
WHERE game = 'numbers4' AND digits = '0097' ORDER BY draw_number;
```

## 再取得・再生成

既存 `requirements.txt` のPython環境とPlaywright Chromiumを利用する。

```sh
python scripts/build_history.py --fetch
python -m pytest -q
python scripts/verify_history.py
```

公式の過去一覧からリンクを列挙し、1ページずつ取得する。公式画面の
描画が完了し、表示された回号範囲が全件揃ってからキャッシュへ保存する。
旧ページとCSVから動的に描画されるページの両方を扱う。

`history-cache/` はGit対象外のローカル取得証拠。同じURLの取得済みページは
再取得しないため、中断後に再開できる。オフライン再生成は以下。

```sh
python scripts/build_history.py
```

これは収集時点の固定スナップショットを作るコマンド。月次ページの再取得・
新しい回の定期追記はまだ自動配信ワークフローへ接続していない。
将来は取得済みの過去回を固定し、最新結果のみ追加する。既存回と異なる結果は
自動上書きせず、出典を再確認する訂正手順を用意する。

## 品質検査

- 各ゲームの第1回日付、回号1から収録末尾までの連続性。
- 同一回号・日付の重複と内容の矛盾、日付の逆転、未来日。
- ロトの数字個数、範囲、組内重複、本数字とボーナスの重複。
- ナンバーズの桁数・先頭ゼロ、ビンゴ5のマス別範囲。
- 現行 `public/draws.json` の全レコードとの一致。
- SQLiteのintegrity_checkとforeign_key_check。
- DBから全件を読み戻して取得スナップショットと照合し、出典の関連づけも検証。
- 同じキャッシュからのオフライン再生成が同じSHA-256になること。

全件を検証してから一時DBを正式ファイルへ置き換える。不完全な取得結果を
完全なデータベースとして出力しない。全回を別の独立データ提供者と突合した
わけではなく、出典はみずほ銀行の公式表示が中心。

## 出典・利用条件の確認記録

2026-09-05に以下を確認。

- https://www.mizuhobank.co.jp/takarakuji/check/loto/backnumber/index.html
- https://www.mizuhobank.co.jp/takarakuji/check/numbers/backnumber/index.html
- https://www.mizuhobank.co.jp/takarakuji/check/bingo/backnumber/index.html
- https://www.mizuhobank.co.jp/notice/index.html

利用案内には掲載内容の著作権についての記載があるが、アプリ同梱向けの
明示的なオープンデータライセンスは確認できていない。公開結果という事実と、
データベースの再配布条件は別に扱う。今回はローカルでの構築・検証用で、
このDBのGitHub Pagesへの公開、アプリへの同梱・ストア配布は未実施。
