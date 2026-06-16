# 設定與環境變數

## 必要變數

至少要設定：

- `DERIBIT_ENV=mainnet`
- `DERIBIT_CLIENT_ID`
- `DERIBIT_CLIENT_SECRET`
- `OPTION_STRATEGY` 選擇 `naked_short`、`bull_put_spread` 或 `covered_call`（舊名 `naked_short_put` / `naked_short_call` 會被解析為 `naked_short`）— **投資人 layout 下由 `accounts.toml` 的 `strategy` 注入，子帳 env 不必填**
- 其餘共用參數可直接從 [`.env.example`](../.env.example) 複製

## 相關文件

- **建議**：一位投資人一個目錄、底下最多數個子帳戶（各跑不同策略），見 [`config/investors/_example/`](../config/investors/_example/)
- **投資人前置作業**（入金、子帳、API Key、Zero Trust Email）：[`investor-onboarding-zh-TW.md`](investor-onboarding-zh-TW.md)
- **管理方新增投資人**（CLI：`investor init` / `import-handoff` / `validate`）：[`operator-onboarding-zh-TW.md`](operator-onboarding-zh-TW.md)
- **目錄架構與 legacy 遷移**：[`repo-layout-zh-TW.md`](repo-layout-zh-TW.md)
- **績效費口徑**：[`investor-fee-disclosure-zh-TW.md`](investor-fee-disclosure-zh-TW.md)

策略 tuning 在 [`config/shared/strategies/`](../config/shared/strategies/)；子帳至少放憑證與資金規模，有需要時也可在同一檔覆寫少數策略鍵（見下方載入順序）。

## Investor / Sub-account Layout（建議）

```text
config/shared/.env.defaults             # 可選：全投資人共用 fallback（gitignore）
config/shared/strategies/.env.<strategy>  # 策略參數（無 API key）
config/platform/fee-payout-addresses.toml  # 管理方外部收款地址（投資人季結算付費）
config/investors/<investor_id>/
  accounts.toml                         # 策略子帳清單（通常 ≤ 3）
  accounts/.env.<slug>                  # 策略子帳：憑證、STATE_FILE、資金規模

# 同 repo 多投資人時，執行期資料依 investor_id 分目錄（互不干擾）：
.state/investors/<investor_id>/<slug>.json
.state/investors/<investor_id>/<slug>.heartbeat.json   # live cycle 心跳（watchdog 用）
.state/investors/<investor_id>/<slug>.trade_journal.db
data/frontend_ledger/<investor_id>/[<slug>/]equity_*.jsonl
data/frontend_ledger/<investor_id>/metrics.db
data/frontend_ledger/<investor_id>/portal_snapshots.db
data/frontend_ledger/_shared/market.db          # 全 repo 共用 spot / IVR 快照
logs/live/<investor_id>/<slug>.log
```

### 設定載入順序

低 → 高；**子帳 env 最後**，可覆蓋 shared 策略檔：

1. `config/shared/.env.defaults`（可選；`config/shared/defaults.env` 為 legacy 別名，載入時會提示）
2. `config/investors/<id>/.env.investor`（可選；`investor.env` 為 legacy 別名）
3. `config/shared/strategies/.env.<OPTION_STRATEGY>` — 策略骨架（市場、擔保幣、put/call 方向）
4. `config/shared/strategies/tiers/<OPTION_STRATEGY>/.env.<tier>` — 風險分級參數（`low` | `medium` | `high`）
5. `accounts/.env.<slug>`（策略子帳；`accounts/<slug>.env` 為 legacy 別名）

若使用 repo 根目錄單一 `.env`（非 `config/investors/.../accounts/`），則仍為：`.env.defaults` → 該 `.env` → 策略 profile（profile 優先於重疊鍵）。

**季結算付費地址**：複製 `config/platform/fee-payout-addresses.toml.example` 為 `fee-payout-addresses.toml` 並填入 USDC／USDT／USDE 鏈上地址；`./bot investor validate` 若缺少此檔會 warning。

### 子帳 env 建議欄位

`DERIBIT_*`、`ORDER_LABEL_PREFIX`、`STATE_FILE`、`REFERENCE_CAPITAL_USDC`、`TARGET_PORTFOLIO_APR`、`TOP_N`

策略與風險分級由 **`accounts.toml`** 的 `strategy` / `risk_tier` 決定（`risk_tier` 預設 `medium`）；子帳 env 不再填 `OPTION_STRATEGY` 或 `RISK_TIER`。

### 風險分級（low / medium / high）

每種策略在 `config/shared/strategies/tiers/<strategy>/` 下有 **`.env.low`、`.env.medium`、`.env.high`** 三份完整參數；策略骨架（`.env.<strategy>`）只放 `OPTION_STRATEGY`、市場 profile、擔保幣等與 tier 無關的設定。

```
config/shared/strategies/
  .env.covered_call              # 骨架
  tiers/covered_call/
    .env.low
    .env.medium
    .env.high
```

投資人選擇方式：在 **`accounts.toml`** 每列加 `risk_tier = "low"` 或 `"high"`（省略則預設 `medium`）。

新建投資人範例：

```bash
# 全部中風險（預設）
./bot investor init alice --strategies naked,covered_call

# 全部低風險
./bot investor init alice --strategies naked,covered_call --risk-tier low

# 各策略不同風險
./bot investor init alice --strategies naked,covered_call,bull_put --risk-tiers naked:low,covered_call:high,bull_put:medium
```

子帳 env 仍可在最後一層覆寫任意鍵（例如只調 `REFERENCE_CAPITAL_USDC`）。

| 分級 | 典型差異 |
|------|----------|
| **low** | 較低 delta（深 OTM）、**較低** `MIN_NET_APR`（安全 strike 可接受薄 premium）、較緊 IM/MM、較少 `MAX_GROUPS_PER_CURRENCY` |
| **medium** | 標準 delta 與 APR 門檻 |
| **high** | 較高 delta（近 strike）、**較高** `MIN_NET_APR`（高風險必須高權利金補償）、較寬 IM 上限 |

共用 pacing／流動性門檻在 `config/shared/.env.defaults`；某子帳若要偏離 tier 預設，在 `accounts/.env.<slug>` 寫入同名鍵即可覆蓋。

建立本機投資人目錄：

```bash
cp -R config/investors/_example config/investors/youming
# 依 accounts/.env.<slug>.example 建立 .env.naked 等並填入 API key
```

CLI 用法見 [CLI 指令](cli-zh-TW.md)。

## 同 repo 多投資人（frontend / live 隔離）

- **策略狀態**：`STATE_FILE` 建議設為 `.state/investors/<investor_id>/<slug>.json`（範本已採此格式）。
- **Dashboard**：`./bot --investor <id> frontend` 會自動寫入 `data/frontend_ledger/<investor_id>/`；多子帳時再分子目錄 `<slug>/`。`metrics.db` 為 `data/frontend_ledger/<investor_id>/metrics.db`；投資人 portal 預組 bundle 為 `portal_snapshots.db`；BTC/ETH spot 與 IVR 共用 `data/frontend_ledger/_shared/market.db`。
- **Live 監督**：`python scripts/run_live_profiles.py --investor <id> --restart-failed` 只跑 `accounts.toml` 內 **`enabled = true` 且 `live_enabled = true`**（預設 true）且有 API 的子帳；日誌在 `logs/live/<investor_id>/<slug>.log`。若要 dashboard 繼續追蹤某策略但不自動下單，在該列設 `live_enabled = false`（仍須 `enabled = true`）。429 等暫時性 API 錯誤 bot 會退避重試，子程序異常退出時監督腳本會自動重啟該 profile。macOS 常駐範本：[`live-profiles-launchd-zh-TW.md`](live-profiles-launchd-zh-TW.md)。
- **不可混用**：同一個 `frontend` 行程不要同時載入兩位投資人的 env；請各開一個 `--port`（對外 Tunnel 亦一人一路）。
- **覆寫路徑**（進階）：`FRONTEND_LEDGER_DIR`、`FRONTEND_METRICS_DB`；live 則用 `--log-dir`。
- **從舊版 flat ledger 遷移**（曾寫入 `data/frontend_ledger/naked/` 等）：搬到 `data/frontend_ledger/<investor_id>/naked/`，`metrics.db` 搬到 `data/frontend_ledger/<investor_id>/metrics.db`。可執行 `./scripts/cleanup_legacy_layout.sh` 自動清理本機 legacy 產物（詳見 [`repo-layout-zh-TW.md`](repo-layout-zh-TW.md)）。

## 績效費 NAV 快照（Performance fee）

計費口徑見 [`investor-fee-disclosure-zh-TW.md`](investor-fee-disclosure-zh-TW.md)：`NAV_perf`（扣備兌現貨）、`AUM_mgmt`（含現貨）、HWM、10% 績效費。收取方式：季末管理方在策略子帳 **Trade** 將獲利現貨兌 **USDC／USDT／USDE**，投資人確認帳單後轉至 `fee-payout-addresses.toml` 之外部地址（見 [`investor-onboarding-zh-TW.md`](investor-onboarding-zh-TW.md) 第五、六節）。

1. 在 `config/investors/<id>/.env.investor` 設定備兌現貨數量與費率（範本：[`config/investors/_example/.env.investor.example`](../config/investors/_example/.env.investor.example)）。
2. **首次** `./bot --investor <id> fee-snapshot` 會從 **accounts.toml 內所有已設 API 的子帳**加總 `deposit` + `withdrawal` + `transfer`（BTC/ETH/USDC 各帳本，再換算 USDC）。**子帳互轉**在加總時會互相抵銷；**主帳入金再轉入子帳**時，即使沒有主帳 API，也會算在子帳的 inbound `transfer` 上。若首次結果有誤可 `./bot --investor <id> fee-flow-report` 核對，再以 `--force-bootstrap` 重跑。

```
初始 HWM（NAV_perf）= max(0, 累計淨入金 USDC 等價 − 備兌現貨 USDC 等價)
```

若需手動指定起始高水位，可設 `INITIAL_HWM_NAV_PERF`。交易流水預設自 **2026-01-01 UTC** 起掃描（覆寫：`FEE_FLOW_START_DATE=YYYY-MM-DD`）。備兌現貨可選填 `COLLATERAL_SPOT_BTC` / `COLLATERAL_SPOT_ETH`（多數投資人留 0 即可）。

**Covered call 獲利兌 USDT（可選）**：在 `.env.investor` 設 `COVERED_CALL_PROFIT_SWEEP_ENABLED=true`（預設 false）。啟用後，covered_call 子帳在 income exit（take profit / time exit / early exit）獲利平倉時，會將該筆 native premium profit 自動 market 賣成 **USDT**；僅賣該筆 realized PnL，不動約定備兌現貨。修改後需**重啟**該子帳 live bot。Dashboard 會唯讀顯示開關狀態與每筆 sweep 紀錄。季末付費時若 profit 已在 USDT，通常只需兌剩餘 BTC/ETH 獲利。

**Covered call ITM spot exit**：tier profile 預設 `COVERED_CALL_SPOT_EXIT_ENABLED=true`。ITM **結算後** pending spot 會 market 賣 **BTC_USDT / ETH_USDT**（與 profit sweep 相同 quote）。賣出數量 = `cover − settlement_loss`：優先讀 Deribit **transaction log** 的 settlement/delivery 扣幣，否則以 intrinsic 估算；避免結算已扣幣後 oversell。若改走 **robust exit**（`COVERED_CALL_ROBUST_EXIT_ENABLED=true`），會先買回 short call 再賣 spot，且不扣 settlement loss。

啟用 `COVERED_CALL_SPOT_EXIT_ENABLED` 或 `COVERED_CALL_PROFIT_SWEEP_ENABLED` 時，config 會**自動**把 **USDT** 加入 `TRADED_COLLATERALS`，無需手動改 strategy env。

**Spot 滑價保護**：策略 profile 可設 `COVERED_CALL_SPOT_MAX_SLIPPAGE_PCT`（例如 `0.005` = 相對 mark 最多賣低 0.5%）。大於 0 時，market 賣出會改為 **limit IOC**，底價 = `mark × (1 − pct)`；若當下 best bid 低於底價則**跳過**並下個 cycle 重試（profit sweep / ITM spot exit 維持 pending）。

3. 手動或排程快照，寫入 `data/fee_ledger/<investor_id>/snapshots.db`：

```bash
./bot --investor an fee-snapshot          # 立即快照
./bot --investor an fee-status            # 查看 HWM / 最近快照 / 歷史結算
./bot --investor an fee-settle --period 2026-Q1

# 自訂區間結算 + 報表（PDF/MD/CSV）；淨申赎預設自 Deribit 流水計算
./bot --investor an fee-flow-report --from 2026-05-01 --to 2026-05-21   # 結算前預覽入金/提領
./bot --investor an fee-settle-period --from 2026-05-01 --to 2026-05-21
# 投資人從 Deribit 提領去付外部績效費（非贖回本金）時，排除該筆對 NAV 的影響：
./bot --investor an fee-settle-period --from 2026-05-01 --to 2026-05-21 --fee-payment-usdc 1500
./bot --investor an fee-settle-period --to now                    # --to 之前最近一筆快照 → 現在
./bot --investor an fee-settle-period --to 2026-05-21 --no-persist  # 試算，不寫入 HWM
./bot --investor an fee-report --kind settlement --period 20260501T000000Z_20260521T235959Z

# English reports for investors (PDF + Markdown; PDF is the primary deliverable)
./bot --investor an fee-report --kind initial
./bot --investor an fee-report --kind settlement --period 2026-Q1
./bot --investor an fee-report --kind initial --format pdf    # PDF only
./bot --investor an fee-report --kind initial --format csv   # Excel-friendly CSV only
./bot --investor an fee-report --kind initial --format all    # PDF + MD + CSV

# cron（建議每日 23:55 UTC，季末再 fee-settle）
python3 scripts/snapshot_investor_fee_nav.py --investor an
```

- **Initial report**: auto-written on first `fee-snapshot` bootstrap to `data/fee_ledger/<id>/reports/initial/initial-YYYYMMDD.{pdf,md,-flows.csv,-summary.csv}`.
- **Quarterly report**: auto-written after `fee-settle` to `data/fee_ledger/<id>/reports/YYYY-MM-DD/settlement-YYYY-QN.{pdf,md,-flows.csv,-summary.csv}` (date folder = period end, UTC).
- **Period settlement** (`fee-settle-period`): same date layout under `reports/YYYY-MM-DD/settlement-<period-id>.*`.
- **CSV**: `-summary.csv` = Day A/B balances, deposits, withdrawals, earned, fees; `-flows.csv` = period cash movements; `-trades.csv` = closed option groups in the period.

快照會合併該投資人所有 enabled 子帳（同 API key 去重），並依 Deribit 指數價計算 `NAV_perf = 總權益 − 備兌現貨 USDC 等價`。

## 共用 env 範例

以下參數以 `REFERENCE_CAPITAL_USDC=1000` 的小資金試跑為基準，預設偏保守。實際上線前先用 dry-run 觀察 `scan --json` 的候選數量、rejection reason 與成交價差；若候選太少，優先放寬 `MIN_LIQUID_EXPIRIES_REQUIRED`、流動性門檻或 DTE，不要先大幅提高 delta。

`.env` 建議只放環境、憑證與所有策略共用的基礎參數：

```dotenv
# --- Environment & credentials ---
DERIBIT_ENV=mainnet
DERIBIT_CLIENT_ID=
DERIBIT_CLIENT_SECRET=

# --- Portfolio scope ---
MANAGED_CURRENCIES=BTC,ETH
SCAN_UNDERLYINGS=BTC,ETH
MIN_BOOK_EQUITY_USDC=50
REFERENCE_CAPITAL_USDC=1000
TARGET_PORTFOLIO_APR=0.25
TOP_N=5

# --- Entry window ---
PUT_DTE_MIN=10
PUT_DTE_MAX=21
MIN_LIQUID_EXPIRIES_REQUIRED=2

# --- Liquidity gates ---
INVERSE_MIN_OPEN_INTEREST=20
INVERSE_MAX_SPREAD_RATIO=0.12
INVERSE_MIN_BOOK_NOTIONAL_USDC=3000
LINEAR_MIN_OPEN_INTEREST=8
LINEAR_MAX_SPREAD_RATIO=0.14
LINEAR_MIN_BOOK_NOTIONAL_USDC=4000

# --- APR gates ---
MIN_NET_APR=0.08
TARGET_NET_APR_MIN=0.10
TARGET_NET_APR_MAX=0.18

# --- Book risk caps ---
PER_LEG_IM_CAP_PUT=0.15
PER_LEG_IM_CAP_CALL=0.12
EXPIRY_IM_CAP=0.30
BOOK_IM_TARGET=0.35
BOOK_IM_HARD=0.45
BOOK_MM_TARGET=0.22
BOOK_MM_HARD=0.33

# --- Position management ---
TP_CAPTURE_PCT=0.55
ENABLE_EARLY_EXIT=true
EARLY_EXIT_REMAINING_APR=0.08
EARLY_EXIT_MIN_PROFIT_CAPTURE=0.45
EARLY_EXIT_MAX_SPREAD_RATIO=0.06
TIME_EXIT_DTE=4
SOFT_DEFENSE_LOSS_PCT=0.30
HARD_STOP_LOSS_PCT=0.45
# 防守確認窗：delta/虧損觸發需連續 N 個 cycle 成立才平倉（1=立即，舊行為）。
# 調高可避免被單一快照尖峰「砍在谷底」；建議 2~3。
DEFENSE_CONFIRM_CYCLES=1
# 以 mark 公允價（而非 best-ask）判斷虧損型止損，避免 IV/價差瞬間擴大假觸發。
DEFENSE_TRIGGER_USE_MARK=true

# --- Regime / circuit breakers ---
INDEX_DRAWDOWN_ELEVATED_PCT=0.035
INDEX_DRAWDOWN_CRISIS_PCT=0.055
DVOL_ELEVATED_MULTIPLIER=1.20
DVOL_CRISIS_MULTIPLIER=1.50
HALT_DRAWDOWN_PCT=0.025
HARD_DERISK_DRAWDOWN_PCT=0.06
HARD_DERISK_MAINTENANCE_MARGIN_RATIO=0.33
HARD_DERISK_ON_CRISIS_OPEN_GROUP=false

# --- Hedging / pacing ---
ENABLE_PERP_HEDGE=false
# Hedge-first：hard 觸發時用 perp 對沖中性化並 hold，不在谷底認賠（需 ENABLE_PERP_HEDGE=true，
# 僅 naked_short / bull_put_spread 生效）。HEDGE_GIVEUP_LOSS_PCT 為災難回補底線（0=不設）。
HEDGE_FIRST_ON_HARD=false
HEDGE_GIVEUP_LOSS_PCT=0
# Per-position 對沖：每個 position 各自對沖自身 delta（soft 對沖 SOFT_HEDGE_NEUTRALIZE_PCT、
# hard 對沖 100%），引擎再把同幣種所有 group 的目標加總成單一 perp 委託；反彈時隨 delta
# 縮小自動回補、連續 RECOVERY_NORMAL_CYCLES 輪未觸發就解除。false=沿用幣種淨 delta 對沖。
PER_POSITION_HEDGE=false
SOFT_HEDGE_NEUTRALIZE_PCT=0.7
MAX_CONCURRENT_GROUPS=6
MAX_GROUPS_PER_CURRENCY=3
ENTRY_COOLDOWN_MINUTES=20
COOLDOWN_HOURS=12
RECOVERY_NORMAL_CYCLES=3
ENABLE_NAKED_TOPUP=false
ENABLE_ADOPT_EXCHANGE_POSITIONS=true

# --- Execution / state ---
OPTION_FEE_RATE=0.0003
OPTION_FEE_CAP_RATE=0.125
# 帳戶 trading-fee 折扣（0.10 = 實付牌價 90%）。預設自註冊日起算 6 個月（`OPTION_FEE_DISCOUNT_ANCHOR=registration` + `OPTION_FEE_DISCOUNT_REGISTRATION_MS`）。
# OPTION_FEE_DISCOUNT_RATE=0.10
# OPTION_FEE_DISCOUNT_MONTHS=6
# OPTION_FEE_DISCOUNT_ANCHOR=registration
# OPTION_FEE_DISCOUNT_REGISTRATION_MS=0
EXIT_BUFFER_RATIO=0.03
SHORT_ENTRY_WAIT_SECONDS=120
ORDER_POLL_SECONDS=10
POLL_SECONDS_NORMAL=15
POLL_SECONDS_STRESS=5
ORDER_LABEL_PREFIX=naked_short
REQUEST_TIMEOUT_SECONDS=20
STATE_FILE=.state/investors/<investor_id>/naked.json
```

策略專屬 tuning 在 `config/shared/strategies/` 骨架 + `tiers/<strategy>/` 分級檔；切換策略時改 **`accounts.toml` 的 `strategy`**（或新增子帳 slug），並使用對應的 `STATE_FILE`。

### State 分流

多投資人 layout；`<slug>` 對應 `accounts.toml` 的 slug，如 `naked`、`covered_call`、`bull_put`：

```text
covered_call:     STATE_FILE=.state/investors/<investor_id>/covered_call.json      ORDER_LABEL_PREFIX=covered_call
naked_short:      STATE_FILE=.state/investors/<investor_id>/naked.json             ORDER_LABEL_PREFIX=naked_short
bull_put_spread:  STATE_FILE=.state/investors/<investor_id>/bull_put.json          ORDER_LABEL_PREFIX=bull_put_spread
```

> **Legacy**：單一 `.env` 工作流曾用 `.state/<strategy>.json`（如 `.state/naked_short.json`）；新部署請勿使用，詳見 [`repo-layout-zh-TW.md`](repo-layout-zh-TW.md)。

## 策略 profile 與 tier

投資人 layout 下，策略參數分兩層：

1. **骨架** — `config/shared/strategies/.env.<strategy>`：只放 `OPTION_STRATEGY`、市場 profile、擔保幣、`SHORT_OPTION_SIDE` 等與 tier 無關的設定。
2. **風險分級** — `config/shared/strategies/tiers/<strategy>/.env.{low,medium,high}`：delta / OTM / APR / IM / covered-call spot exit 等完整 tuning。

`accounts.toml` 的 `strategy` 決定骨架；`risk_tier`（預設 `medium`）決定 tier 檔。子帳 env **不必**填 `OPTION_STRATEGY` 或 `RISK_TIER`；需要偏離 tier 時在最後一層覆寫同名鍵即可。

### 骨架一覽

| 策略 | 骨架檔 | 重點 |
|------|--------|------|
| `naked_short` | [`.env.naked_short`](../config/shared/strategies/.env.naked_short) | `linear_usdc`；`TRADED_COLLATERALS=USDC`；`SHORT_OPTION_SIDE=both` |
| `bull_put_spread` | [`.env.bull_put_spread`](../config/shared/strategies/.env.bull_put_spread) | `linear_usdc`；`TRADED_COLLATERALS=USDC`；`SHORT_OPTION_SIDE=put` |
| `covered_call` | [`.env.covered_call`](../config/shared/strategies/.env.covered_call) | `inverse_native`；`TRADED_COLLATERALS=BTC,ETH`；`SHORT_OPTION_SIDE=call` |

各 tier 的 delta、APR、IM 白話對照見 [風險分級與 APR 說明](investor-risk-tiers-apr-zh-TW.md)；**以 tier 檔為準**，勿沿用下方 legacy 單檔範例中的舊數字。

### Covered call tier 共通預設

三個 tier 目前皆：

- `COVERED_CALL_SPOT_EXIT_ENABLED=true`
- `COVERED_CALL_ROBUST_EXIT_ENABLED=false`（主路徑為 settlement pending → spot；robust 需另行開啟）
- `PUT_DTE_MIN=7`、`PUT_DTE_MAX=35`

### Legacy 單檔 workflow

若仍使用 repo 根目錄單一 `.env`（非 `config/investors/...`），可參考 [`.env.example`](../.env.example) 的 fallback 區塊；delta / APR 仍以策略 tier 檔為 canonical source。

```dotenv
# 骨架範例（covered_call）— 完整 tuning 見 tiers/covered_call/.env.{low,medium,high}
OPTION_STRATEGY=covered_call
OPTION_MARKETS_PROFILE=inverse_native
TRADED_COLLATERALS=BTC,ETH
SHORT_OPTION_SIDE=call
```

## 憑證需求

- `ping` 可以不帶私有憑證
- `status`、`enter-best --live`、`manage --live`、`run --live`、`panic-close --live`、`close-position --live`、`cancel` 需要私有憑證
- Deribit 衍生品交易是否可用，取決於你的帳戶資格與司法管轄限制
