# 本地 Dashboard

`./bot frontend` 會啟一個本地 FastAPI server + 純 HTML 單頁前端，把 `status` /
`report` / `stress-current` / `STATE_FILE` 整合成一頁式 dashboard，
明確區分 **BTC / ETH / USDC** 三本位帳戶（由 `PortfolioSnapshot` 內建分桶），顯示：

- 三張 book card：USDC-equivalent equity、原幣 equity、day P&L、IM/MM ratio、delta、regime
- 投組總覽 USDC card：total equity、total profit（lifetime + 30d window）、lifetime/window APR、win rate、avg holding days
- 圖表：open max loss vs book equity（USDC）、累積 realized PnL、每日 PnL + 30d MA、rolling APR
- Open spreads 表 / Recent closed trades 表
- ITM 之後的 **現金擔保賣權（CSP）**：與 covered call 同一套策略卡／持倉卡／活動紀錄（獨立策略 id `cash_secured`）。開倉會顯示履約價、DTE、權利金、進場年化；活動與持倉標 CSP chip、來自 ITM group。CSP **ITM** 到期補回現貨時會顯示「補回現貨」／「已補回現貨」
- 黑天鵝壓力測試卡（同 `./bot stress-current`，會依 `OPTION_STRATEGY` 顯示 `naked_short` / `bull_put_spread` / `covered_call` 的風險解讀）

## 啟動

```bash
pip install -r requirements.txt
./bot frontend                       # 預設 http://127.0.0.1:8765
./bot frontend --port 9000           # 換埠
./bot frontend --no-scheduler        # 關掉背景 equity ledger
./bot --investor youming frontend
```

## 管理者後台（所有投資人 frontend）

`./bot admin` 會在 **本機** 開一個獨立控制台（預設 `http://127.0.0.1:8750`），從 `config/platform/registry.toml` 列出所有投資人，探活各自 dashboard，並可嵌入該人的 ops 頁（`/index.html`）。

```bash
./bot admin                  # http://127.0.0.1:8750
./bot admin --port 8750
```

- **只綁 127.0.0.1**。不要把這個埠寫進 Cloudflare Tunnel / Access；投資人頁維持各自 hostname。
- 非 loopback bind 必須加 `--allow-public`（仍不該對外）。
- 可從後台 **啟動 / 停止 / 重啟** 該投資人 frontend（走既有 launchd：`./bot investor frontend …`）。
- 交易鈕只在嵌入的 ops 卡片上：**Close**（開倉 group）、**CSP關倉補現貨**（開倉 CSP：市價平 put 後用出場報價幣補 cover，通常是 `BTC_USDC` / `ETH_USDC`）、與 **Recover market**（ITM 未買回、且沒有開倉 CSP）。頂列另有 **Panic close**。都是先 Preview，再輸入 `LIVE` 才實單。Recover / CSP 一鍵會先取消該 group 已掛的自動限價單再市價。CSP 一鍵會先把 wheel 標成 `operator_csp_abort_restore`，避免 live `manage` 在平倉後立刻再賣 put。**不會**出現在 `investor.html`。

左側選投資人後，右側 iframe 開的是本機 `http://127.0.0.1:<frontend_port>/index.html`；該 frontend 沒起來時會顯示啟動提示。

預設背景 scheduler 每 `FRONTEND_SNAPSHOT_INTERVAL_SEC` 秒（預設 300）讀一次帳戶
快照，append 到 `data/frontend_ledger/<investor_id>/`（多子帳時為 `.../<investor_id>/<slug>/equity_<UTC date>.jsonl`）。
沒設 `DERIBIT_CLIENT_ID/SECRET` 時 scheduler 自動跳過，但 server
依然可看 closed groups / 累積 PnL / APR 圖。
前端頁面資料刷新有 3 分鐘節流上限；自動刷新與手動 `Refresh` 都會套用同一個限制。

多子帳 dashboard 建議 `./bot --investor <id> frontend`，或 `--account-env-files` 傳入**同一位**投資人的多個 `accounts/.env.<slug>`。

## 背景快照與快取

`frontend` 啟動後（`--no-scheduler` 除外）會跑數個背景 scheduler，寫入 disk 並供 API / 投資人 portal 讀取：

| 資料 | 路徑 | 環境變數（預設） |
|------|------|------------------|
| Equity ledger（帳戶快照 JSONL） | `data/frontend_ledger/<id>/[<slug>/]equity_*.jsonl` | `FRONTEND_SNAPSHOT_INTERVAL_SEC`（300s） |
| Market 快照（BTC/ETH spot、IVR） | `data/frontend_ledger/_shared/market.db` | `MARKET_SNAPSHOT_INTERVAL_SEC`（300s）、`MARKET_SNAPSHOT_RETENTION_DAYS`（7） |
| Portal disk 快照 | `data/frontend_ledger/<id>/portal_snapshots.db` | 每 `snapshot_kind` 只留最新 1 列；`PORTAL_SNAPSHOT_DISK_INTERVAL_SEC`（300s）、`PORTAL_SNAPSHOT_DISK_RETENTION_DAYS`（1，後備） |
| Portal live 快照 | 同上（`snapshot_kind=live`） | 同上；`PORTAL_SNAPSHOT_LIVE_INTERVAL_SEC`（600s）、`PORTAL_SNAPSHOT_LIVE_RETENTION_DAYS`（1，後備） |

投資人 portal（`investor.html`）優先讀 `portal_snapshots.db` 的預組 bundle（`source=portal_cache`），並以 `FRONTEND_INVESTOR_STATUS_CACHE_TTL_SEC`（預設 180s）作為 live 快照有效期限；背景 warm 週期由 `FRONTEND_BUNDLE_WARM_INTERVAL_SEC`（預設 90s）控制，避免每次刷新都打 Deribit。組裝 portal payload 時會遞迴截斷超過 2KB 的字串（例如膨脹的 `spot_exit_reason`），不改交易 state。

Ledger 每列可含 `equity_native_by_book`（原幣 equity）；舊列若只有 USDC 等價 `equity_by_book`，可用 `scripts/backfill_ledger_equity_native.py` 回填（詳見 [`scripts/README.md`](../scripts/README.md)）。

## API 存取控制（選用）

Dashboard 預設**沒有**自己的認證，對外時依賴 Cloudflare Access 擋在前面；本機使用維持原行為。若要加一層縫隙防護（例如 Access 設錯路徑、或本機埠不慎暴露），可設定共用 token：

| 環境變數 | 預設 | 說明 |
|----------|------|------|
| `DASHBOARD_API_TOKEN` | 空（停用） | 設定後，所有 `/api/*` 與 `/ws/*` 都要帶 `Authorization: Bearer <token>` 或 `X-Dashboard-Token: <token>`；WebSocket 另可用 `?token=`。靜態 HTML/JS/CSS 仍公開。缺 token 回 `401`。 |
| `DASHBOARD_API_TOKEN_EMBED` | `false` | `true` 時 server 會把 token 寫進 `index.html` / `investor*.html` 的 `<meta name="dashboard-api-token">`，前端 JS 自動帶上。**只在 Cloudflare Access 後面才有意義**（能載入 HTML 的人就能拿到 token）；主要用於只有營運者看的 ops dashboard。 |
| `DASHBOARD_CORS_ORIGINS` | 空（不掛 CORS） | 逗號分隔的允許 origin。同源部署不需要；只有把 HTML 放到別的網域（`dashboard-api-base` meta）時才設。允許 `GET,POST` 與 `Authorization` / `X-Dashboard-Token` header。 |

前端 JS 取 token 的順序：`<meta name="dashboard-api-token">` → `window.__DASHBOARD_TOKEN__` → `localStorage.dashboard_api_token`（未 embed 時可在瀏覽器 console 手動 `localStorage.setItem("dashboard_api_token", "...")`）。

其他相關行為：

- `/api/trade_journal/sync`（手動觸發 journal 同步，會寫 DB 並打 Deribit）只接受 **POST**；GET 回 `405`。
- `/api/health` 只回 `state_file` / `ledger_dir` / `metrics_db` 的**檔名**與 `*_present` 布林，不再回絕對路徑。
- 靜態檔採白名單：只服務 `index.html` / `investor.html` / `investor.zh.html` / `admin.html`、`app.js` / `app-investor.js`、`styles.css` / `tailwind.css` / `tokens.css`、`favicon.svg` 與 `/vendor/*`；`frontend/` 下其他檔案（`src/`、`node_modules/`、`package-lock.json`…）一律 `404`。
- `/api/dashboard_bundle` 回 weak `ETag`，帶 `If-None-Match` 命中時回 `304`。

管理者後台（`./bot admin`）對應的 knob：

| 環境變數 | 預設 | 說明 |
|----------|------|------|
| `ADMIN_CONSOLE_TOKEN` | 空（停用） | 設定後所有 `/api/admin/*` 需 `X-Admin-Token: <token>` 或 `Authorization: Bearer`。`admin.html` 遇到 `401` 會跳出一次輸入框並存進 `localStorage.admin_console_token`。 |
| `ADMIN_CONSOLE_TOKEN_EMBED` | `false` | `true` 時把 token 寫進 `admin.html` 的 `<meta name="admin-console-token">`。 |

後台除了檢查 `Host` header，也會檢查 **實際連線來源位址** 必須是 loopback（`127.0.0.1` / `::1`），除非啟動時加 `--allow-public`。交易動作（panic-close / close-position / spot-restore / csp-abort-restore / frontend start|stop|restart）只接受 POST。若投資人 frontend 設了 `DASHBOARD_API_TOKEN`，請在跑 `./bot admin` 的環境也設同一個值，探活（`/api/health`）才會帶 token；嵌入的 ops iframe 則需該 frontend 開 `DASHBOARD_API_TOKEN_EMBED=true` 或在該 origin 的 `localStorage` 放 token。

## 多名投資人與對外存取

**多名投資人**（各 `config/investors/<id>/` 一份資料）若需各自專屬對外網址：請為每位投資人各跑一個 `frontend`（例如不同 `--port`），再以 reverse proxy／Tunnel 將不同子網域指到對應埠；細節見 [cloudflare-tunnel-investor.md](cloudflare-tunnel-investor.md)。

家用或無固定公網 IP 時，若要對投資人提供固定 **HTTPS** 連結，可使用 **Cloudflare Named Tunnel**（本機維持 `127.0.0.1` 即可）：步驟、`config.yml` 範例、launchd 與 Access 建議見同一份文件。

## macOS 常駐

**一鍵啟停全部 dashboard（launchd）**：`./bot investor frontend start|stop|restart|status`（依 `config/platform/registry.toml` 的 `frontend_enabled`）；包裝腳本 `./scripts/frontend_launchd_all.sh start`。

**Cloudflare Tunnel（launchd）**：`./bot investor tunnel start|stop|restart|status`（執行 `cloudflared tunnel --config ~/.cloudflared/config.yml run`）；包裝腳本 `./scripts/tunnel_launchd.sh start`。須在 frontend 之後啟動；手動前景 run 見 [cloudflare-tunnel-investor.md](cloudflare-tunnel-investor.md#五tunnel-run手動驗證)。

**一鍵啟停全部 live bot（launchd）**：`./bot investor live start|stop|restart|status`（依 `live_enabled`）；包裝腳本 `./scripts/live_launchd_all.sh start`。細節見 [live-profiles-launchd-zh-TW.md](live-profiles-launchd-zh-TW.md)。

## 相關文件

- 多投資人資料隔離：[`configuration-zh-TW.md`](configuration-zh-TW.md#同-repo-多投資人frontend--live-隔離)
- CLI 啟動範例：[`cli-zh-TW.md`](cli-zh-TW.md#儀表板與多子帳-live)
- 前端 build / e2e：[`frontend/README.md`](../frontend/README.md)
