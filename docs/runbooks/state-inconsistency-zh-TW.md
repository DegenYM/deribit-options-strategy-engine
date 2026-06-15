# State 與交易所不一致

## 症狀

- Dashboard 持倉與 Deribit 網頁不同
- Log 出現 `reconcile`、`phantom`、`adopt` 相關訊息
- 重複 entry 或漏平倉

## 立即檢查

1. `./bot status --env-file config/investors/<id>/accounts/.env.<slug>` 對照 Deribit UI
2. 看 state 檔：`.state/investors/<id>/<slug>.json`
3. 確認 `ENABLE_ADOPT_EXCHANGE_POSITIONS` 是否符合預期（`config/shared/.env.defaults` 或子帳 env）

## 處理步驟

| 步驟 | 動作 |
|------|------|
| 1 | **先停 live**（若持倉異常）：`./bot investor live stop --investor <id>` 或停單一 profile |
| 2 | 備份 state：`cp .state/investors/<id>/<slug>.json .state/investors/<id>/<slug>.json.bak.<date>` |
| 3 | 執行 `./bot manage --env-file ...`（dry-run）觀察 reconcile 動作 |
| 4 | 若需強制對齊：在確認 Deribit 為準後，必要時 `./bot panic-close --live`（見 panic runbook） |
| 5 | 重啟 live 並監看 2–3 個 cycle |

## 預防

- 勿同時對同一子帳跑兩個 live bot
- 手動在 Deribit 下單後，等下一 cycle adopt 或手動 manage
- 定期看 `trade_journal` / dashboard realized 是否連續

## 升級

若 state 已 corrupt（load 時出現 `.corrupt.*` 備份），保留備份檔並從最近已知良好備份還原，或聯絡維護者重建 journal。
