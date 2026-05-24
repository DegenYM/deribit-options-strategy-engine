# 管理方：新增投資人 Runbook（Phase 1）

投資人仍依 [`investor-onboarding-zh-TW.md`](investor-onboarding-zh-TW.md) 完成 Deribit 設定；管理方用 CLI 標準化目錄、registry、憑證匯入與驗證。

**設計原則**：`config/platform/registry.toml` 只管營運（埠、hostname、Access email）；`config/investors/<id>/accounts.toml` 只管策略 manifest——**不合併**。

## 0. 一次性準備

```bash
cd /path/to/deribit-options-strategy-engine
cp config/platform/registry.toml.example config/platform/registry.toml
# 編輯 [platform]：repo_root、python_bin、domain、next_frontend_port
```

## 1. 建立投資人骨架

```bash
./bot investor init alice --strategies naked,covered_call \
  --display-name "Alice" \
  --email "alice@example.com"
```

會建立：

- `config/investors/alice/`（`accounts.toml`、各策略 `.env.*`、**`.env.fee`** 費用專戶）
- `registry.toml` 新增 `[[investors]]`（含 `frontend_port`、`hostname`）
- `config/platform/generated/launchd/com.deribit.*.alice.plist`

### Fee 專戶（每位投資人必建）

| 項目 | 說明 |
|------|------|
| Deribit 子帳名稱 | 建議 `fee` |
| 本機 env | `accounts/.env.fee`（`ACCOUNT_ROLE=fee`） |
| 是否在 `accounts.toml` | **否** — 不參與策略 live / frontend 聚合 |
| API 權限 | Account=read、Wallet=read_write、**Trade=none** |
| 憑證匯入 | `handoff.toml` 的 `[fee]` 區塊（見下節） |
| 用途 | 投資人季結算後將帳單 USDC 劃轉至此；管理方收取管理費／績效費 |
| 禁止 | `./bot run --env-file .../.env.fee`（CLI 會拒絕） |

`investor init` 會自動建立空白 `.env.fee`；`import-handoff` 寫入 `[fee].client_id/secret`。

## 2. 投資人交接

複製 [`config/handoff/handoff.template.toml`](../config/handoff/handoff.template.toml) 給投資人填寫（經安全管道交回，勿用聊天明文 Secret）。

```bash
./bot investor import-handoff /secure/path/alice-handoff.toml
```

## 3. 驗證與 initial HWM

```bash
./bot investor validate alice
```

驗證通過且已打 Deribit API 時，會**自動**：

1. 從各策略子帳交易紀錄彙總淨申赎（USDC 等值）
2. 扣除 BTC／ETH 淨申赎作為備兌現貨基數，計算 **Initial HWM (NAV_perf)**
3. 寫入 `data/fee_ledger/alice/snapshots.db`，並產生 `data/fee_ledger/alice/reports/initial/`（PDF／MD／CSV）

若已在 `.env.investor` 設定 `INITIAL_HWM_NAV_PERF`，則以該值為準（不掃交易紀錄）。

僅檢查檔案與 manifest（不打 API、不 bootstrap HWM）：

```bash
./bot investor validate alice --no-api
```

略過自動 bootstrap、或事後手動重跑：

```bash
./bot investor validate alice --no-bootstrap-hwm
./bot investor bootstrap-hwm alice          # 僅 bootstrap（已存在則跳過）
./bot investor bootstrap-hwm alice --force  # 強制重算（慎用）
```

等同於 `./bot fee-snapshot --investor alice` 的首次 HWM bootstrap；日常排程仍用 `scripts/snapshot_investor_fee_nav.py`。

## 4. 常駐（launchd）

已產生的 plist 在 `config/platform/generated/launchd/`。安裝範例：

```bash
INVESTOR=alice
REPO_ROOT="/path/to/deribit-options-strategy-engine"
mkdir -p "$REPO_ROOT/logs/live/$INVESTOR" "$REPO_ROOT/logs/frontend/$INVESTOR"
cp "$REPO_ROOT/config/platform/generated/launchd/com.deribit.live.${INVESTOR}.plist" \
   ~/Library/LaunchAgents/
cp "$REPO_ROOT/config/platform/generated/launchd/com.deribit.frontend.${INVESTOR}.plist" \
   ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deribit.live.${INVESTOR}.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deribit.frontend.${INVESTOR}.plist
```

Cloudflare Tunnel / Access 仍依 [`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md)（Phase 2 再自動化 `provision`）。

## 一鍵管理所有投資人 frontend（launchd）

依 `registry.toml` 內 `frontend_enabled = true` 的列，批次啟動／停止／重啟／查狀態（會同步 plist 到 `~/Library/LaunchAgents/`）：

```bash
./bot investor frontend start      # 啟動全部
./bot investor frontend stop       # 停止全部
./bot investor frontend restart    # 重啟全部（已載入則 kickstart）
./bot investor frontend status     # 狀態 + 本機 /api/health

# 等同包裝腳本
./scripts/frontend_launchd_all.sh start
```

**Live bot（實單監督）** — 依 `live_enabled = true` 批次管理 `com.deribit.live.*` LaunchAgent：

```bash
./bot investor live start      # 啟動全部
./bot investor live stop       # 停止全部
./bot investor live restart    # 重啟全部
./bot investor live status     # 狀態 + supervisor.log 是否已有 started pid=

./scripts/live_launchd_all.sh start
```

單一投資人：加 `--investor pat`。略過 supervisor 檢查：`--no-supervisor-check`。

## 常用指令

| 指令 | 說明 |
|------|------|
| `./bot investor list` | 列出 registry + 本機 `config/investors/` |
| `./bot investor frontend start` | 一次啟動所有 `frontend_enabled` 的 dashboard |
| `./bot investor live start` | 一次啟動所有 `live_enabled` 的 live 監督 |
| `./bot investor render-launchd alice` | 重產 launchd plist（改埠時加 `--port`） |
| `./bot investor bootstrap-hwm alice` | 僅建立 initial HWM（`validate` 成功時已自動執行） |
| `./bot investor init ... --no-register` | 只建目錄、不寫 registry |

## 既有投資人（youming / jack / an）

可將現有列補進 `registry.toml`（範例檔已含參考列），**不必**改動既有 `accounts.toml`。
