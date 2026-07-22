# 新投資人快速上線 Checklist（管理方）

目標：從「決定接投資人」到「對方能開 dashboard」可在同一套指令內完成。完整細節仍見 [`operator-onboarding-zh-TW.md`](operator-onboarding-zh-TW.md)。

## 為什麼會 404？

本站 Cloudflare Tunnel 是 **遠端管理（remotely managed）**：

| 設定位置 | 作用 |
|----------|------|
| `~/.cloudflared/config.yml` | 本機備份／文件化；**單獨改它不夠** |
| Cloudflare Tunnel Public Hostname（遠端 config） | **真正生效**的 ingress；缺 hostname → **HTTP 404** |
| DNS CNAME → `*.cfargotunnel.com` | 網域能解析 |
| Zero Trust Access Application | 誰能登入 |

因此新增投資人後，**必須**跑 `./bot investor provision-tunnel`（會同步本機 + 遠端 + DNS）。

---

## 0. 一次性（每台 Mac 只需一次）

```bash
cd /path/to/deribit-options-strategy-engine
# registry.toml 已含：
#   domain / hostname_template / tunnel_name / repo_root / python_bin
cloudflared tunnel login   # 產生 ~/.cloudflared/cert.pem（provision-tunnel 會用）
./bot investor tunnel status
```

`[platform]` 建議：

```toml
domain = "debopt.com"
hostname_template = "{id}-portfolio.debopt.com"
tunnel_name = "debopt-jack"
```

特殊 hostname（例如 eugene → `yoeugene-portfolio.debopt.com`）在 `registry.toml` 該列手動覆寫 `hostname`。

---

## 1. 新投資人（複製即用）

把 `alice` / Email / 策略換成實際值：

```bash
ID=alice
EMAIL="alice@example.com"
STRATEGIES=covered_call   # 或 naked,covered_call 等

# 1) 骨架 + registry 列（自動分配 frontend_port + hostname）
./bot investor init "$ID" \
  --strategies "$STRATEGIES" \
  --display-name "Alice" \
  --email "$EMAIL"

# 若 hostname 要自訂（非 {id}-portfolio.debopt.com）：
# 編輯 config/platform/registry.toml 該列的 hostname，再繼續

# 2) 匯入 Deribit 憑證（安全管道交回的 handoff）
./bot investor import-handoff /secure/path/${ID}-handoff.toml

# 3) 驗證 API + initial HWM
./bot investor validate "$ID"

# 4) 啟動本機 dashboard（先於 tunnel 對外）
./bot investor frontend start --investor "$ID"
./bot investor frontend status --investor "$ID"
# 或：curl -sS "http://127.0.0.1:<frontend_port>/api/health"  （埠見 registry.toml）

# 5) 同步 Tunnel：本機 config.yml + Cloudflare 遠端 ingress + DNS
./bot investor provision-tunnel --investor "$ID"
# 預覽： ./bot investor provision-tunnel --investor "$ID" --dry-run

# 6) 確認 tunnel 行程在跑（全站一個）
./bot investor tunnel status
# 若剛改過遠端 config，通常數秒內自動套用；必要時：
# ./bot investor tunnel restart

# 7) Cloudflare Access（仍手動，一人一 Application）
# Zero Trust → Access → Applications → Add → Self-hosted
# Domain = registry 的 hostname
# Policy Allow = dashboard_email（與 --email 相同）
# 檢查清單：docs/cloudflare-access-checklist-zh-TW.md

# 8) （可選）開 live 實單
./bot investor live start --investor "$ID"
```

給投資人的書籤：

`https://<hostname>/` 或 `https://<hostname>/investor.html`

---

## 2. 驗證矩陣（上線前打勾）

| # | 檢查 | 預期 |
|---|------|------|
| 1 | `./bot investor frontend status --investor <id>` | healthy |
| 2 | 本機 `curl http://127.0.0.1:<port>/investor.html` | 200 |
| 3 | `./bot investor provision-tunnel --investor <id> --dry-run` | 無 example.com；remote 已含 hostname |
| 4 | 外網開 `https://<hostname>/investor.html` | Access 登入頁（302），**不是** Chrome HTTP 404 |
| 5 | 用 `dashboard_email` 登入 | 看到 investor dashboard |
| 6 | 無痕／錯 Email | Access denied |

---

## 3. 常見錯誤

| 症狀 | 原因 | 處理 |
|------|------|------|
| Chrome **HTTP ERROR 404** | 遠端 Tunnel ingress 缺 hostname | `./bot investor provision-tunnel --investor <id>` |
| Access 登入後空白／502 | frontend 未起或埠錯 | `frontend status`；對照 registry port |
| Access denied | Email 不在 policy | 對齊 `dashboard_email` |
| 只改了 `config.yml` 仍 404 | 遠端 config 覆蓋本機 | 一定要跑 provision-tunnel（含 remote） |

---

## 4. 指令速查

| 指令 | 用途 |
|------|------|
| `./bot investor init <id> ...` | 建目錄 + registry |
| `./bot investor import-handoff …` | 寫入 API 憑證 |
| `./bot investor validate <id>` | 驗證 + HWM |
| `./bot investor frontend start --investor <id>` | 本機 dashboard |
| `./bot investor provision-tunnel --investor <id>` | **對外網址必跑** |
| `./bot investor tunnel status` | cloudflared 健康 |
| `./bot investor live start --investor <id>` | 實單監督 |

相關文件：[`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md)、[`cloudflare-access-checklist-zh-TW.md`](cloudflare-access-checklist-zh-TW.md)、[`runbooks/tunnel-failure-zh-TW.md`](runbooks/tunnel-failure-zh-TW.md)。
