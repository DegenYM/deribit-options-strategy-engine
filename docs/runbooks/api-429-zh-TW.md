# Deribit 429 / API 連續失敗

## 症狀

- Log：`transient exchange error`、HTTP 429
- Telegram：`Repeated Deribit API errors`（≥5 次連續）
- Heartbeat 仍更新但 `last_error` 有值（bot 活著但 API 不穩）

## 立即檢查

1. Deribit [status 頁](https://status.deribit.com/) 或社群是否 outage
2. 是否多 process 打同一 API key（live + 手動 script + 其他機器）
3. `logs/live/<investor_id>/<slug>.log` 退避間隔是否拉長

## 處理步驟

| 步驟 | 動作 |
|------|------|
| 1 | **通常先觀察**：engine 會退避重試，`exchange_throttle` 全进程 pacing |
| 2 | 若 429 持續 >15 分鐘：停非必要 frontend stress prefetch、停多餘 bot |
| 3 | 確認只有一個 `run_live_profiles` / launchd 監督該 investor |
| 4 | 必要時 `./bot investor live restart --investor <id>` |
| 5 | 仍失敗 → 檢查 IP 是否被限流、是否需換 testnet/mainnet env |

## 設定

- `REQUEST_TIMEOUT_SECONDS`（`defaults.env`）
- 勿在短時間大量跑 backfill / 手動 script 與 live 共用 key

## 升級

API 恢復後仍無法 auth → 見 [credential-rotation-zh-TW.md](credential-rotation-zh-TW.md)。
