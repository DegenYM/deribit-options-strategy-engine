# Incident Runbooks

Live 營運故障照表操課。搭配 [Telegram 告警](../telegram-alerts-zh-TW.md) 與 heartbeat watchdog 使用。

| 情境 | Runbook |
|------|---------|
| State 與交易所不一致 | [state-inconsistency-zh-TW.md](state-inconsistency-zh-TW.md) |
| Hard derisk / panic close | [panic-and-derisk-zh-TW.md](panic-and-derisk-zh-TW.md) |
| Deribit 429 / API 連續失敗 | [api-429-zh-TW.md](api-429-zh-TW.md) |
| Cloudflare Tunnel / Dashboard 無法連線 | [tunnel-failure-zh-TW.md](tunnel-failure-zh-TW.md) |
| API 憑證輪替 | [credential-rotation-zh-TW.md](credential-rotation-zh-TW.md) |
| Heartbeat 過期（bot 可能卡住） | 見下方 |

## Heartbeat 過期

**自動處理**：`run_live_profiles.py --restart-failed`（launchd 範本預設）會自行偵測 heartbeat 過期（預設 600 秒、可用 `LIVE_HEARTBEAT_STALE_SECONDS` 或 `--heartbeat-stale-seconds` 調），對卡住的子程序 SIGTERM → 30 秒後 SIGKILL → 依指數退避（15s 起、上限 600s）重啟，並發 Telegram `[CRITICAL] Live bot heartbeat stale; restarting`（`event_key=live_heartbeat_stale_restart:<env>`）。收到此告警時：

1. 看 `logs/live/<investor_id>/supervisor.log`：確認 `restarted ... pid=` 有出現、`attempt=` 是否持續增加（連續失敗代表重啟後仍卡住 → 往下查根因）
2. 看子帳 log：`logs/live/<investor_id>/<slug>.log`（輪替檔 `<slug>.log.1..N`）
3. 看 heartbeat：`.state/investors/<investor_id>/<slug>.heartbeat.json` 的 `ts_ms` / `last_error`
4. 若 supervisor.log 出現 `heartbeat missing ... not restarting`：heartbeat 路徑與 bot 實際 `STATE_FILE` 不符（或舊版 bot 尚未載入 heartbeat 邏輯），不會自動重啟，請手動 `./bot investor live restart --investor <id>` 並核對 env 內 `STATE_FILE`
5. 要暫停自動重啟（例如手動 debug 中）：在 plist 加 `--no-heartbeat-restart` 後 `launchctl kickstart -k`

**手動處理 / 未啟用自動重啟時**：

1. 確認 launchd / `run_live_profiles.py` 是否在跑：`./bot investor live status`
2. 手動重啟：`./bot investor live restart --investor <id>`
3. 定期純告警檢查（建議每 5 分鐘，不會重啟）：

```bash
python scripts/check_live_heartbeat.py
```

可設 `LIVE_HEARTBEAT_STALE_SECONDS=600`（預設 10 分鐘）調整門檻。

**重啟後仍顯示 `reason=missing`**：舊行程必須重啟才會載入 heartbeat 寫入邏輯；重啟後 bot 會在 `run --live` 啟動時立刻寫入 `cycle=0`，並在每個 cycle 開始時更新 timestamp。若 watchdog 在重啟後立刻跑，請等至少一個完整 cycle（帳戶較重時首 cycle 可能需 2–5 分鐘，遇 429 更久）再判定 STALE。
