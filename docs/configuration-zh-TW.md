# 設定與環境變數

## 必要變數

至少要設定：

- `DERIBIT_ENV=mainnet`
- `DERIBIT_CLIENT_ID`
- `DERIBIT_CLIENT_SECRET`
- `ENABLE_PERP_HEDGE=false` 可停用 perpetual hedge；目前預設即為關閉
- `OPTION_STRATEGY` 選擇 `naked_short`、`bull_put_spread` 或 `covered_call`（舊名 `naked_short_put` / `naked_short_call` 會被解析為 `naked_short`）
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
config/shared/defaults.env              # 可選：全投資人共用 fallback
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
logs/live/<investor_id>/<slug>.log
```

### 設定載入順序

低 → 高；**子帳 env 最後**，可覆蓋 shared 策略檔：

1. `config/shared/defaults.env`（可選；`config/shared/.env.defaults` 為 legacy 別名，載入時會提示）
2. `config/investors/<id>/.env.investor`（可選；`investor.env` 為 legacy 別名）
3. `config/shared/strategies/.env.<OPTION_STRATEGY>` — 策略骨架（市場、擔保幣、put/call 方向）
4. `config/shared/strategies/tiers/<OPTION_STRATEGY>/.env.<tier>` — 風險分級參數（`low` | `medium` | `high`）
5. `accounts/.env.<slug>`（策略子帳；`accounts/<slug>.env` 為 legacy 別名）

若使用 repo 根目錄單一 `.env`（非 `config/investors/.../accounts/`），則仍為：defaults → 該 `.env` → 策略 profile（profile 優先於重疊鍵）。

**季結算付費地址**：複製 `config/platform/fee-payout-addresses.toml.example` 為 `fee-payout-addresses.toml` 並填入 USDC／USDT／USDE 鏈上地址；`./bot investor validate` 若缺少此檔會 warning。

### 子帳 env 建議欄位

`DERIBIT_*`、`OPTION_STRATEGY`、`RISK_TIER`（`low` | `medium` | `high`，預設 `medium`）、`ORDER_LABEL_PREFIX`、`STATE_FILE`、`REFERENCE_CAPITAL_USDC`、`TARGET_PORTFOLIO_APR`、`TOP_N`

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

投資人選擇方式（二擇一，建議用 manifest）：

1. **`accounts.toml`** 每列加 `risk_tier = "low"`（或 `medium` / `high`）
2. **子帳 env** 寫 `RISK_TIER=low`（init 時會自動寫入）

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
| **low** | 較低 delta、較高 `MIN_NET_APR`、較緊 IM/MM、較少 `MAX_GROUPS_PER_CURRENCY` |
| **medium** | 標準進攻性（原策略 profile 數值） |
| **high** | 較高 delta、較低 APR 門檻、較寬 IM 上限 |

共用 pacing／流動性門檻在 `config/shared/.env.defaults`；某子帳若要偏離 tier 預設，在 `accounts/.env.<slug>` 寫入同名鍵即可覆蓋。

建立本機投資人目錄：

```bash
cp -R config/investors/_example config/investors/youming
# 依 accounts/.env.<slug>.example 建立 .env.naked 等並填入 API key
```

CLI 用法見 [CLI 指令](cli-zh-TW.md)。

## 同 repo 多投資人（frontend / live 隔離）

- **策略狀態**：`STATE_FILE` 建議設為 `.state/investors/<investor_id>/<slug>.json`（範本已採此格式）。
- **Dashboard**：`./bot --investor <id> frontend` 會自動寫入 `data/frontend_ledger/<investor_id>/`；多子帳時再分子目錄 `<slug>/`。`metrics.db` 為 `data/frontend_ledger/<investor_id>/metrics.db`。
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

**Covered call ITM spot exit**：策略 profile 設 `COVERED_CALL_SPOT_EXIT_ENABLED=true` 時，ITM 退場（robust 或 settlement pending）也會 market 賣成 **BTC_USDT / ETH_USDT**，與 profit sweep 相同 quote。啟用 `COVERED_CALL_SPOT_EXIT_ENABLED` 或 `COVERED_CALL_PROFIT_SWEEP_ENABLED` 時，config 會**自動**把 **USDT** 加入 `TRADED_COLLATERALS`，無需手動改 strategy env。

**Spot 滑價保護**：策略 profile 可設 `COVERED_CALL_SPOT_MAX_SLIPPAGE_PCT`（例如 `0.005` = 相對 mark 最多賣低 0.5%）。大於 0 時，market 賣出會改為 **limit IOC**，底價 = `mark × (1 − pct)`；若當下 best bid 低於底價則**跳過**並下個 cycle 重試（profit sweep / ITM spot exit 維持 pending）。

3. 手動或排程快照，寫入 `data/fee_ledger/<investor_id>/snapshots.db`：

```bash
./bot --investor an fee-snapshot          # 立即快照
./bot --investor an fee-status            # 查看 HWM / 最近快照 / 歷史結算
./bot --investor an fee-settle --period 2026-Q1 --net-flow-usdc 0

# 自訂區間結算 + 報表（PDF/MD/CSV）；淨申赎預設自 Deribit 流水計算
./bot --investor an fee-settle-period --from 2026-05-01 --to 2026-05-21
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

策略專屬值請放在對應 profile 檔；切換策略時改 account env 的 `OPTION_STRATEGY`，並同步使用該策略自己的 `STATE_FILE`。

### State 分流

多投資人 layout；`<slug>` 對應 `accounts.toml` 的 slug，如 `naked`、`covered_call`、`bull_put`：

```text
covered_call:     STATE_FILE=.state/investors/<investor_id>/covered_call.json      ORDER_LABEL_PREFIX=covered_call
naked_short:      STATE_FILE=.state/investors/<investor_id>/naked.json             ORDER_LABEL_PREFIX=naked_short
bull_put_spread:  STATE_FILE=.state/investors/<investor_id>/bull_put.json          ORDER_LABEL_PREFIX=bull_put_spread
```

> **Legacy**：單一 `.env` 工作流曾用 `.state/<strategy>.json`（如 `.state/naked_short.json`）；新部署請勿使用，詳見 [`repo-layout-zh-TW.md`](repo-layout-zh-TW.md)。

## 策略 profile 範例

下列三組策略參數在 `config/shared/strategies/`（`.env.naked_short`、`.env.bull_put_spread`、`.env.covered_call`）。

### `naked_short`

尾端風險最大，但收益性排在 covered call 之後、bull put spread 之前。`SHORT_OPTION_SIDE` 可選 `put` / `call` / `both`：選 `both` 時 put 與 call 候選會用同一個 sort key 一起排序、競爭 `TOP_N` 名額。

```dotenv
OPTION_STRATEGY=naked_short
OPTION_MARKETS_PROFILE=all
TRADED_COLLATERALS=BTC,ETH,USDC
SHORT_OPTION_SIDE=both

SHORT_PUT_DELTA_MIN=0.08
SHORT_PUT_DELTA_MAX=0.14
PREFERRED_SHORT_PUT_DELTA_MIN=0.09
PREFERRED_SHORT_PUT_DELTA_MAX=0.12
PUT_OTM_MIN=0.09
PUT_OTM_MAX=0.24

BTC_PUT_DELTA_MIN=0.08
BTC_PUT_DELTA_MAX=0.15
BTC_PREFERRED_PUT_DELTA_MIN=0.09
BTC_PREFERRED_PUT_DELTA_MAX=0.12
BTC_PUT_OTM_MIN=0.08
BTC_PUT_OTM_MAX=0.22
BTC_PREFERRED_OTM_MIN=0.10
BTC_PREFERRED_OTM_MAX=0.16

ETH_PUT_DELTA_MIN=0.07
ETH_PUT_DELTA_MAX=0.13
ETH_PREFERRED_PUT_DELTA_MIN=0.08
ETH_PREFERRED_PUT_DELTA_MAX=0.11
ETH_PUT_OTM_MIN=0.10
ETH_PUT_OTM_MAX=0.24
ETH_PREFERRED_OTM_MIN=0.12
ETH_PREFERRED_OTM_MAX=0.18

MIN_NET_APR=0.08
TARGET_NET_APR_MIN=0.10
TARGET_NET_APR_MAX=0.18
PER_LEG_IM_CAP_PUT=0.14
BOOK_IM_TARGET=0.30
BOOK_IM_HARD=0.40
SOFT_DEFENSE_DELTA=0.18
HARD_DEFENSE_DELTA=0.25
SOFT_DEFENSE_LOSS_PCT=0.25
HARD_STOP_LOSS_PCT=0.40
```

### `bull_put_spread`

有 long put 保護腿，但雙腿 debit 會犧牲淨 credit，所以 APR 門檻最低。

```dotenv
OPTION_STRATEGY=bull_put_spread
OPTION_MARKETS_PROFILE=all
TRADED_COLLATERALS=BTC,ETH,USDC
SHORT_OPTION_SIDE=put

SHORT_PUT_DELTA_MIN=0.08
SHORT_PUT_DELTA_MAX=0.16
PREFERRED_SHORT_PUT_DELTA_MIN=0.09
PREFERRED_SHORT_PUT_DELTA_MAX=0.13
PUT_OTM_MIN=0.08
PUT_OTM_MAX=0.23

BTC_PUT_DELTA_MIN=0.09
BTC_PUT_DELTA_MAX=0.17
BTC_PREFERRED_PUT_DELTA_MIN=0.10
BTC_PREFERRED_PUT_DELTA_MAX=0.14
BTC_PUT_OTM_MIN=0.07
BTC_PUT_OTM_MAX=0.21
BTC_PREFERRED_OTM_MIN=0.09
BTC_PREFERRED_OTM_MAX=0.15

ETH_PUT_DELTA_MIN=0.07
ETH_PUT_DELTA_MAX=0.14
ETH_PREFERRED_PUT_DELTA_MIN=0.08
ETH_PREFERRED_PUT_DELTA_MAX=0.12
ETH_PUT_OTM_MIN=0.10
ETH_PUT_OTM_MAX=0.23
ETH_PREFERRED_OTM_MIN=0.12
ETH_PREFERRED_OTM_MAX=0.17

BULL_PUT_LONG_DELTA_MIN=0.025
BULL_PUT_LONG_DELTA_MAX=0.07
MIN_NET_APR=0.06
TARGET_NET_APR_MIN=0.08
TARGET_NET_APR_MAX=0.14
PER_LEG_IM_CAP_PUT=0.16
BOOK_IM_TARGET=0.35
BOOK_IM_HARD=0.45
SOFT_DEFENSE_DELTA=0.22
HARD_DEFENSE_DELTA=0.32
```

### `covered_call`

只使用既有 BTC/ETH 現貨 cover，建議只開 inverse native book；這是最積極的收益 profile。

```dotenv
OPTION_STRATEGY=covered_call
OPTION_MARKETS_PROFILE=inverse_native
TRADED_COLLATERALS=BTC,ETH
SHORT_OPTION_SIDE=call
MIN_NET_APR=0.12
TARGET_NET_APR_MIN=0.15
TARGET_NET_APR_MAX=0.28

SHORT_CALL_DELTA_MIN=0.18
SHORT_CALL_DELTA_MAX=0.38
PREFERRED_SHORT_CALL_DELTA_MIN=0.22
PREFERRED_SHORT_CALL_DELTA_MAX=0.32
CALL_OTM_MIN=0.025
CALL_OTM_MAX=0.18

BTC_CALL_DELTA_MIN=0.18
BTC_CALL_DELTA_MAX=0.38
BTC_PREFERRED_CALL_DELTA_MIN=0.22
BTC_PREFERRED_CALL_DELTA_MAX=0.32
BTC_CALL_OTM_MIN=0.025
BTC_CALL_OTM_MAX=0.16
BTC_PREFERRED_CALL_OTM_MIN=0.04
BTC_PREFERRED_CALL_OTM_MAX=0.10

ETH_CALL_DELTA_MIN=0.16
ETH_CALL_DELTA_MAX=0.34
ETH_PREFERRED_CALL_DELTA_MIN=0.20
ETH_PREFERRED_CALL_DELTA_MAX=0.30
ETH_CALL_OTM_MIN=0.035
ETH_CALL_OTM_MAX=0.20
ETH_PREFERRED_CALL_OTM_MIN=0.05
ETH_PREFERRED_CALL_OTM_MAX=0.12

SOFT_DEFENSE_DELTA_CALL=0.35
HARD_DEFENSE_DELTA_CALL=0.50
PER_LEG_IM_CAP_CALL=0.20

COVERED_CALL_SPOT_EXIT_ENABLED=false
COVERED_CALL_ROBUST_EXIT_ENABLED=false
COVERED_CALL_ROBUST_EXIT_DTE=0.5
COVERED_CALL_ITM_BUFFER_PCT=0
# ITM robust exit 的確認窗（cycle 數）；留空 = 沿用 DEFENSE_CONFIRM_CYCLES。
# 避免 index 短暫戳破 strike 的 wick 就買回 call + 賣 spot 賣在頂點。
# COVERED_CALL_ITM_CONFIRM_CYCLES=
COVERED_CALL_SPOT_ORDER_TYPE=market

MAX_GROUPS_PER_CURRENCY=3
MAX_CONCURRENT_GROUPS=6
# 槽位分配（預設 true）：每筆進場 ≈ 剩餘 cover / 剩餘 MAX_GROUPS_PER_CURRENCY 槽位
# COVERED_CALL_SLOT_SIZING=true
```

## 憑證需求

- `ping` 可以不帶私有憑證
- `status`、`enter-best --live`、`manage --live`、`run --live`、`panic-close --live`、`close-position --live`、`cancel` 需要私有憑證
- Deribit 衍生品交易是否可用，取決於你的帳戶資格與司法管轄限制
