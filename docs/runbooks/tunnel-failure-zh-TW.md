# Cloudflare Tunnel / Dashboard 失效

## 症狀

- 投資人無法開 dashboard URL
- `./bot investor frontend status` 顯示異常
- Tunnel 程序 exit（與 bot 無直接關係，但營運需可見）

## 立即檢查

1. `./bot investor frontend status`（或 launchd frontend plist）
2. `./bot investor tunnel status`（launchd + `http://127.0.0.1:20241/metrics`）
3. 本機 curl：`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<frontend_port>/api/dashboard_bundle`
4. Cloudflare Zero Trust / Tunnel 控制台該 connector 是否在線
5. `config/platform/registry.toml` 的 `hostname`、`frontend_port`、`tunnel_name` 是否正確

## 處理步驟

| 步驟 | 動作 |
|------|------|
| 1 | 重啟 frontend：`./bot investor frontend restart --investor <id>` |
| 2 | 若外網 **HTTP 404**：多半是遠端 Tunnel ingress 缺 hostname → `./bot investor provision-tunnel --investor <id>` |
| 3 | 重啟 tunnel：`./bot investor tunnel restart`（或前景除錯：`cloudflared tunnel --config ~/.cloudflared/config.yml run`） |
| 4 | 查 log：`~/Library/Logs/cloudflared-<tunnel_name>.err.log`；搜尋 `Updated to new configuration` 是否含該 hostname |
| 5 | 確認 Access policy 未誤刪 `dashboard_email` 對應規則 |
| 6 | Bot live 可獨立運行；dashboard 掛掉不必然停 trading |

## 預防

- 新投資人上線一律跑 [`investor-provision-checklist-zh-TW.md`](../investor-provision-checklist-zh-TW.md)
- registry 維護 `dashboard_email` 與 hostname 對照；勿只改本機 `config.yml`

## 升級

本機 port 正常但外網不行 → 幾乎一定是 Tunnel / DNS / Access；保留 cloudflared log 再查 CF 側。
