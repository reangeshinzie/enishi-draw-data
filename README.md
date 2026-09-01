# enishi-draw-data

「えにし」が端末内で数字を照合するための、公開抽せん結果JSONです。

- 一次確認元: みずほ銀行 宝くじ 当せん番号案内
- 自動取得: GitHub Actions上のPlaywright
- 配信: GitHub Pagesの `public/draws.json`
- 個人データ: 氏名、誕生日、縁札、購入分、課金情報は一切扱いません

取得結果は回号、日付、本数字・ボーナス数字の個数、範囲、重複を検証します。取得または検証に失敗した場合は処理を失敗させ、直前の正しいJSONを維持します。

## ローカル確認

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q
python scripts/update_draws.py --games all --months-back 2
```

当せん結果の一致は購入や当せんの証明ではありません。購入券を公式情報と照合してください。
