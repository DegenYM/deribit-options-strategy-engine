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

1. 確認 launchd / `run_live_profiles.py` 是否在跑：`./bot investor live status`
2. 看子帳 log：`logs/live/<investor_id>/<slug>.log`
3. 看 heartbeat：`.state/investors/<investor_id>/<slug>.heartbeat.json` 的 `ts_ms` / `last_error`
4. 手動重啟：`./bot investor live restart --investor <id>`
5. 定期檢查（建議每 5 分鐘）：

```bash
python scripts/check_live_heartbeat.py
```

可設 `LIVE_HEARTBEAT_STALE_SECONDS=600`（預設 10 分鐘）調整門檻。
