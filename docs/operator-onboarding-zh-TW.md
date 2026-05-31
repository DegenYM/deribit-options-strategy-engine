# 管理方：新增投資人 Runbook（Phase 1）

投資人仍依 [`investor-onboarding-zh-TW.md`](investor-onboarding-zh-TW.md) 完成 Deribit 設定；管理方用 CLI 標準化目錄、registry、憑證匯入與驗證。

**設計原則**：`config/platform/registry.toml` 只管營運（埠、hostname、Access email）；`config/investors/<id>/accounts.toml` 只管策略 manifest——**不合併**。

## 0. 一次性準備

```bash
cd /path/to/deribit-options-strategy-engine
cp config/platform/registry.toml.example config/platform/registry.toml
cp config/platform/fee-payout-addresses.toml.example config/platform/fee-payout-addresses.toml
# 編輯 [platform]：repo_root、python_bin、domain、next_frontend_port
# 編輯 fee-payout-addresses.toml：USDC/USDT/USDE 外部收款地址（投資人季結算付費用）
```

## 1. 建立投資人骨架

```bash
./bot investor init alice --strategies naked,covered_call \
  --display-name "Alice" \
  --email "alice@example.com"
```

會建立：

- `config/investors/alice/`（`accounts.toml`、各策略 `accounts/.env.*`）
- `registry.toml` 新增 `[[investors]]`（含 `frontend_port`、`hostname`）
- `config/platform/generated/launchd/com.deribit.*.alice.plist`

### 策略子帳 API（`accounts.toml` 內各策略）

| Deribit scope | 設定 |
|---------------|------|
| Account | read |
| Trade | read_write（期權 + **季末賣現貨兌 USDC／USDT／USDE**） |
| Wallet | **none**（管理方無主帳 API，不做子帳互轉） |

**勿**要求投資人提供主帳 API 或費用子帳 API。

### 季結算收取流程（管理方）

1. 出具帳單（`fee-settle` / 報表 PDF）。  
2. 若有獲利現貨需兌付：在策略子帳將對應部分 **TRADE 成 USDC、USDT 或 USDE**（策略 API：`trade:read_write`）。穩定幣留在策略子帳供雙方在 Deribit 後台對照帳單。  
3. 與投資人對帳，確認金額無誤。  
4. 投資人自**主帳 Withdraw** 或外部錢包，轉至 `fee-payout-addresses.toml` 內指定之**鏈上地址**（幣別、鏈、金額以帳單為準）。

**CLI 範例**（預設 dry-run；加 `--live` 才送單）：

```bash
# 賣現貨兌 USDC（covered_call 子帳最常見）
./bot --investor youming --account covered_call trade-spot \
  --from-currency BTC --amount 0.05 --to USDC --json
./bot --investor youming --account covered_call trade-spot \
  --from-currency BTC --amount 0.05 --to USDC --live --json

# 賣出全部可用 BTC（對齊交易所最小單位）
./bot --investor youming --account covered_call trade-spot \
  --from-currency BTC --all --to USDC --live --json
```

`internal-transfer`（子帳 → 費用子帳）**已非標準流程**：Deribit 子帳互轉需主帳 API，投資人通常不交付主帳金鑰。若你仍持有主帳 API 且需 legacy 劃轉，見 `wallet_ops` 與 `accounts/.env.main`（`investor init` 不再自動建立）。

## 2. 投資人交接

複製 [`config/handoff/handoff.template.toml`](../config/handoff/handoff.template.toml) 給投資人填寫（經安全管道交回，勿用聊天明文 Secret）。

簽約或 onboarding 時，一併提供 [`fee-payout-addresses.toml`](../config/platform/fee-payout-addresses.toml) 中地址的正式清單（PDF／加密信均可）。

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

`validate` 若缺少 `fee-payout-addresses.toml` 會出現 **warning**（不阻擋通過）。

## 4. 常駐（macOS launchd / Linux systemd）

已產生的 plist 在 `config/platform/generated/launchd/`（macOS）；systemd unit 在 `generated/systemd/`（Linux）。

**macOS** 安裝範例：

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

**Linux** 請見 [`live-profiles-systemd-zh-TW.md`](live-profiles-systemd-zh-TW.md)（`./bot investor render-systemd <id>` 產生 unit 後 `systemctl enable --now`）。

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

**Cloudflare Tunnel（cloudflared run）** — 全站**一個** tunnel 行程；讀 `registry.toml` 的 `[platform].tunnel_name`，實際執行 `cloudflared tunnel --config ~/.cloudflared/config.yml run`：

```bash
./bot investor tunnel start      # 啟動（先確認 frontend 已起）
./bot investor tunnel stop       # 停止
./bot investor tunnel restart    # 重啟
./bot investor tunnel status     # launchd + 本機 metrics

./scripts/tunnel_launchd.sh start
```

手動前景測試（不占 launchd）：見 [`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md) 第五節。

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
| `./bot investor tunnel start` | 啟動 cloudflared tunnel run（launchd 常駐） |
| `./bot investor live start` | 一次啟動所有 `live_enabled` 的 live 監督 |
| `./bot investor render-launchd alice` | 重產 launchd plist（改埠時加 `--port`） |
| `./bot investor render-systemd alice` | 重產 systemd unit（Linux；改埠時加 `--port`） |
| `./bot investor bootstrap-hwm alice` | 僅建立 initial HWM（`validate` 成功時已自動執行） |
| `./bot investor init ... --no-register` | 只建目錄、不寫 registry |

## 既有投資人（youming / jack / an）

可將現有列補進 `registry.toml`（範例檔已含參考列），**不必**改動既有 `accounts.toml`。
