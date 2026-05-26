# Cloudflare Access 設定檢查清單

對外開放投資人 dashboard 時，**強烈建議**在 Tunnel 後加 Cloudflare Access，避免僅靠 obscurity 保護 `/investor.html` 與 `/api/*`。

相關文件：

- Tunnel 與多投資人架構：[`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md)
- `registry.toml` 的 `dashboard_email`：[`operator-onboarding-zh-TW.md`](operator-onboarding-zh-TW.md)
- Incident（Tunnel 失效）：[`runbooks/tunnel-failure-zh-TW.md`](runbooks/tunnel-failure-zh-TW.md)

---

## 1. 前置條件

| # | 項目 | 完成 |
|---|------|------|
| 1.1 | 網域 DNS 託管於 Cloudflare | ☐ |
| 1.2 | Named Tunnel 已建立且 `cloudflared` 可連線 | ☐ |
| 1.3 | 每位投資人 frontend 已常駐（launchd / systemd）且本機 `/api/health` 為 200 | ☐ |
| 1.4 | `config/platform/registry.toml` 已填 `hostname`、`frontend_port`、`dashboard_email` | ☐ |

---

## 2. 每位投資人一個 Access Application

**原則**：一位投資人 = 一個 hostname = 一個 Access 應用程式 = 獨立允許名單。

| # | 步驟 | 完成 |
|---|------|------|
| 2.1 | Zero Trust → **Access** → **Applications** → Add application | ☐ |
| 2.2 | 類型選 **Self-hosted** | ☐ |
| 2.3 | Application name：`Deribit dashboard · <investor_id>`（例：`Deribit dashboard · jack`） | ☐ |
| 2.4 | Session Duration：依政策（建議 ≤ 24h；敏感帳戶可更短） | ☐ |
| 2.5 | **Application domain**：填 registry 的 `hostname`（例：`jack.portfolio.example.com`） | ☐ |
| 2.6 | Path：留空（保護整站含 `/api/*`） | ☐ |

---

## 3. Policy（誰能進）

| # | 步驟 | 完成 |
|---|------|------|
| 3.1 | 新增 Policy：`Allow investor <id>` | ☐ |
| 3.2 | Action：**Allow** | ☐ |
| 3.3 | Include：該投資人的 Email（與 `registry.toml` 的 `dashboard_email` 一致） | ☐ |
| 3.4 | 可選 Include：管理方 ops Email（break-glass） | ☐ |
| 3.5 | 確認 **未** 使用「Allow everyone」或過寬的 `@domain.com`（除非刻意共用） | ☐ |
| 3.6 | 預設 deny：僅明確 Allow 的 identity 可通過 | ☐ |

**registry 對照範例**（`config/platform/registry.toml`）：

```toml
[[investors]]
id = "jack"
hostname = "jack.portfolio.example.com"
frontend_port = 8766
dashboard_email = "jack@example.com"   # Access Allow 名單應包含此 Email
frontend_enabled = true
```

---

## 4. 驗證（上線前必做）

| # | 測試 | 預期 | 完成 |
|---|------|------|------|
| 4.1 | 允許名單內 Email 開 `https://<hostname>/investor.html` | 通過 Access 後看到 dashboard | ☐ |
| 4.2 | **非**允許名單 Email（或無痕未登入）開同一 URL | 被 Access 擋下 | ☐ |
| 4.3 | 投資人 A 的 Email 開投資人 B 的 hostname | 仍被擋（B 的 policy 不含 A） | ☐ |
| 4.4 | `GET https://<hostname>/api/health`（已通過 Access 的 session） | 200 | ☐ |
| 4.5 | 直接打本機 `http://127.0.0.1:<port>/`（不經 Tunnel） | 僅本機可達；確認未對公網開 port | ☐ |

---

## 5. 營運與輪替

| # | 項目 | 完成 |
|---|------|------|
| 5.1 | 新增投資人：先完成 Tunnel ingress + frontend，再建 Access Application | ☐ |
| 5.2 | 投資人 Email 變更：同步改 Access policy 與 `registry.toml` | ☐ |
| 5.3 | 離職 / 終止：先 Remove Access policy，再停 frontend / 撤銷 API key | ☐ |
| 5.4 | 憑證輪替：見 [`runbooks/credential-rotation-zh-TW.md`](runbooks/credential-rotation-zh-TW.md) | ☐ |

---

## 6. 常見錯誤

| 症狀 | 可能原因 | 處理 |
|------|----------|------|
| 投資人看到 Cloudflare 403 / Access denied | Email 不在 policy；或登入錯誤 Google/IdP | 核對 `dashboard_email` 與 Access Include |
| 通過 Access 但 dashboard 空白 / API 502 | 本機 frontend 未跑；Tunnel ingress 埠錯 | `./bot investor frontend status`；對照 `registry.toml` 與 `config.yml` |
| A 能看到 B 的資料 | 共用同一 `--investor` 行程或同一 hostname | 每位投資人獨立 frontend + hostname + Access app |
| `/api/*` 未受保護 | Access 只設了 `/investor.html` path | Path 留空或明確包含 `/api` |

---

## 7. 完成標準

- 每位對外投資人有獨立 hostname + Access Application + Allow policy  
- 非授權 identity 無法載入 dashboard 或呼叫 `/api/dashboard_bundle`  
- `registry.toml` 的 `dashboard_email` 與 Access 名單一致且可稽核  
