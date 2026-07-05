# Frontend Uptime 監控

`scripts/check_frontend_uptime.py` 會讀取 `config/platform/registry.toml`，對每個 `frontend_enabled = true` 的投資人檢查 dashboard health endpoint：

```text
GET http://127.0.0.1:<frontend_port>/api/health
```

health endpoint 使用現有 frontend server route：`/api/health`。檢查失敗時，script 會沿用 `scripts/check_live_heartbeat.py` 相同的 Telegram 機制，從 `config/shared/.env.defaults` 載入 `TELEGRAM_ALERTS_ENABLED`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` 與 cooldown 設定。

## 手動執行

```bash
.venv/bin/python scripts/check_frontend_uptime.py --dry-run
```

只檢查單一投資人：

```bash
.venv/bin/python scripts/check_frontend_uptime.py --investor jack --timeout 2 --dry-run
```

JSON 輸出：

```bash
.venv/bin/python scripts/check_frontend_uptime.py --json --dry-run
```

成功時 exit code 為 `0`；有 frontend 失敗時為 `1`；registry 缺失、沒有可檢查的 frontend 等設定問題為 `2`。

## Cron 範例

每 1 分鐘檢查一次本機 frontend，失敗時發 Telegram：

```cron
* * * * * cd /Users/youming/Desktop/deribit_option && .venv/bin/python scripts/check_frontend_uptime.py >> logs/frontend-uptime.log 2>&1
```

Linux VPS 範例：

```cron
* * * * * cd /opt/deribit-options-strategy-engine && /opt/venv/bin/python scripts/check_frontend_uptime.py >> logs/frontend-uptime.log 2>&1
```

若只想先觀察、不發 Telegram：

```cron
* * * * * cd /opt/deribit-options-strategy-engine && /opt/venv/bin/python scripts/check_frontend_uptime.py --dry-run >> logs/frontend-uptime.log 2>&1
```

## 參數

| 參數 | 說明 |
|------|------|
| `--investor <id>` | 只檢查指定投資人；預設檢查 registry 內所有 `frontend_enabled = true` rows |
| `--timeout <秒>` | HTTP timeout；預設 `3` 秒，最低會夾到 `0.1` 秒 |
| `--json` | 輸出 JSON，方便 cron、外部監控或 log collector 解析 |
| `--dry-run` | 顯示失敗但不發 Telegram；仍會用 exit code `1` 表示檢查失敗 |

## 與 Cloudflare Tunnel

本 script 檢查的是 host 本機的 `127.0.0.1:<frontend_port>/api/health`，可確認 frontend process 與 app route 是否正常。Cloudflare Tunnel / Access 的外部可用性建議另外用 Uptime Kuma 或外部 probe 檢查 registry 內的 `hostname`：

```text
https://<hostname>/api/health
```

若 Access policy 會擋匿名 request，外部 probe 需配置 service token 或改檢查 tunnel service 本身。遇到 tunnel 失效時，處理流程可參考 `docs/runbooks/tunnel-failure-zh-TW.md`。

## 告警內容

Telegram 訊息會包含：

- 投資人 id
- health endpoint 與本機 URL
- HTTP status 或錯誤訊息
- registry hostname
- registry frontend port

Telegram cooldown 由 `TELEGRAM_ALERT_COOLDOWN_SECONDS` 控制，避免每分鐘重複洗版。
