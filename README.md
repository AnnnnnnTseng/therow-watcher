# The Row 補貨監控

每小時檢查 The Row 商品庫存，補貨時開 Issue 通知（GitHub 會寄 email）。

- 監控清單：`products.txt`，一行一個商品網址
- 目前狀態：`state.json`（由 workflow 自動更新）
- 排程：`.github/workflows/watch.yml`

手動測試：Actions → The Row 補貨監控 → Run workflow → 勾選「寄一封測試通知」
