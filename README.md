# Deribit Options Strategy Engine

這個 repo 是 `BTC + ETH` 的 Deribit 自動化 option 策略引擎，可透過 `OPTION_STRATEGY` 選擇策略。

GitHub: https://github.com/DegenYM/deribit-options-strategy-engine

This project is not affiliated with or endorsed by Deribit.

核心設計：

- `naked_short`：單腿 short option，依 `SHORT_OPTION_SIDE` 設定為 `put` / `call` / `both`；`both` 時 put 與 call 候選會用同一組排序鍵一起決定 rank，不另外為 call 預留名額。舊名 `naked_short_put` / `naked_short_call` 會自動正規化成 `naked_short`
- `bull_put_spread`：先買 long put 保護腿，再賣 short put，最大虧損以 spread width 封頂
- `covered_call`：只在既有 BTC/ETH 庫存足夠時賣 call，不自動買底層，也不使用 perp 作 cover；可選擇在 ITM 退場時同步賣 Deribit spot
- 可選擇是否啟用 `perp` delta hedge
- `spot` 不參與正常收益流程，只留給異常庫存處理
- 目標是 `1000 USDC` 參考資金下年化淨利 `200 USDC+`
- 預設 `dry-run first`，只有 `--live` 才會真的下單

## Strategy Model

- 掃描 `Deribit Linear USDC Options` 與 `BTC/ETH-settled reversed options`
- 進場窗口預設為 `10-21 DTE`
- short leg 會先過 delta、OTM、OI、book notional、spread ratio、APR 與 book IM/MM 門檻
- `bull_put_spread` 的 long put 以 `BULL_PUT_LONG_DELTA_MIN/MAX` 選擇，同到期且 strike 低於 short put
- `covered_call` 只使用 BTC/ETH 本位 book 的既有可用庫存作 cover，不會自動買現貨或用 perp 補 cover；spot exit 開關預設關閉
- 只做流動性足夠的 short leg：`OI`、`book notional`、`spread ratio` 都要過門檻
- `MIN_LIQUID_EXPIRIES_REQUIRED` 可控制 DTE 視窗內至少需要幾個可交易 expiry 才允許開倉
- regime 分為 `normal / elevated / crisis`
- `crisis` 不開新倉；`hard stop` 直接平倉；`soft trigger` 優先 roll，不行就平倉；`TP` 與 `time exit` 都會主動退場

### Strategies

- `naked_short`：單腿賣 OTM option，依 `SHORT_OPTION_SIDE` 控制方向：
    - `put`：只掃 short put（等同舊版 `naked_short_put`），下跌尾端風險最大。
    - `call`：只掃 short call，上漲尾端風險最大。
    - `both`：put 與 call 候選會合併到同一個 sort key（APR band → preferred delta/OTM → margin efficiency → spread → ...）一起競爭 `TOP_N` 名額；engine 不會強制保留 call 名額。
    建議使用較低 delta、較深 OTM、較低單腿 IM cap。
- `bull_put_spread`：賣較高 strike put，同時買較低 strike put 作保護腿，最大虧損約為 spread width 減淨權利金。因為虧損被 long put 封頂，short put delta 可比 naked short 稍高，但淨權利金、long leg 流動性與 max-loss APR 要一起檢查。
- `covered_call`：只用既有 BTC/ETH 現貨庫存賣 call；現貨 cover 會降低 upside short call 的爆倉型風險，所以 call delta 可選較大。風險是上漲收益被履約價封頂，以及現金/幣本位結算後仍可能留下 spot exposure；若要鎖定 ITM 退場，可開啟 spot exit，robust 模式會先買回 call、再賣 BTC_USDC / ETH_USDC spot。

### Price / Return Sketches

下列圖表是單位化 payoff 示意，用來快速比較到期價格與收益形狀；實際收益仍以 `scan` / `enter-best` 的成交 credit、debit、fee、slippage 與持倉天數為準。

`naked_short`（短 put 範例）：假設 short put strike `K=100`、收到權利金 `P=2`。價格高於 `K` 時收益封頂為權利金，跌破損益兩平點後虧損跟著標的下跌擴大。short call 形狀對稱，只是價格上漲超過 `K` 後虧損擴大。

![Naked short put payoff](docs/strategy-payoffs/naked-short-put.png)

`bull_put_spread`：假設 short put `K=100`、long put `L=90`、淨 credit `P=2`。上方收益同樣封頂，但下跌最大虧損被 long put 限制。

![Bull put spread payoff](docs/strategy-payoffs/bull-put-spread.png)

`covered_call`：假設持有現貨成本 `S0=100`、short call strike `K=110`、收到權利金 `P=2`。權利金提供一點下跌緩衝，但上漲超過 `K` 後總收益被封頂。

![Covered call payoff](docs/strategy-payoffs/covered-call.png)

## Setup

```bash
cd deribit-options-strategy-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Environment

至少要設定：

- `DERIBIT_ENV=testnet` 或 `mainnet`
- `DERIBIT_CLIENT_ID`
- `DERIBIT_CLIENT_SECRET`
- `ENABLE_PERP_HEDGE=false` 可停用 perpetual hedge；目前預設即為關閉
- `OPTION_STRATEGY` 選擇 `naked_short`、`bull_put_spread` 或 `covered_call`（舊名 `naked_short_put` / `naked_short_call` 會被解析為 `naked_short`）
- 其餘共用參數可直接從 [`.env.example`](.env.example) 複製；策略專屬參數已拆到 [`.env.naked_short`](.env.naked_short)、[`.env.bull_put_spread`](.env.bull_put_spread)、[`.env.covered_call`](.env.covered_call)。如果舊版 `.env.naked_short_put` 還留著，找不到 `.env.naked_short` 時會自動 fallback。

### Recommended Env Profiles

設定載入順序是「account env -> `.env.<OPTION_STRATEGY>`」。例如 `.env` 或 `.env.bull_put_spread_sub` 裡設定 `OPTION_STRATEGY=bull_put_spread` 時，程式會自動讀取同目錄的 `.env.bull_put_spread`，並用 profile 內的 delta、OTM、APR、risk、defense、pacing 參數覆蓋 account env 的 fallback 值。

實盤多子帳戶建議使用本機帳戶 env：`.env.covered_call_sub`、`.env.naked_short_sub`、`.env.bull_put_spread_sub`。這些檔案已放進 `.gitignore`，每個檔案只填該 sub account 的 `DERIBIT_CLIENT_ID/SECRET`、`ORDER_LABEL_PREFIX` 與 `STATE_FILE`，策略參數仍由對應 `.env.<strategy>` profile 管理。

以下參數以 `REFERENCE_CAPITAL_USDC=1000` 的小資金試跑為基準，預設偏保守。實際上線前先用 `testnet` 與 dry-run 觀察 `scan --json` 的候選數量、rejection reason 與成交價差；若候選太少，優先放寬 `MIN_LIQUID_EXPIRIES_REQUIRED`、流動性門檻或 DTE，不要先大幅提高 delta。

`.env` 建議只放環境、憑證與所有策略共用的基礎參數：

```dotenv
# --- Environment & credentials ---
DERIBIT_ENV=testnet
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
# Optional per-underlying overrides; BTC_MIN_OPEN_INTEREST / ETH_MIN_OPEN_INTEREST
# apply to both inverse and linear unless the type-specific key is set.
# BTC_MIN_OPEN_INTEREST=20
# ETH_MIN_OPEN_INTEREST=12
# BTC_INVERSE_MIN_OPEN_INTEREST=20
# ETH_INVERSE_MIN_OPEN_INTEREST=12
INVERSE_MAX_SPREAD_RATIO=0.12
INVERSE_MIN_BOOK_NOTIONAL_USDC=3000
LINEAR_MIN_OPEN_INTEREST=8
# BTC_LINEAR_MIN_OPEN_INTEREST=8
# ETH_LINEAR_MIN_OPEN_INTEREST=5
LINEAR_MAX_SPREAD_RATIO=0.14
LINEAR_MIN_BOOK_NOTIONAL_USDC=4000

# --- APR gates ---
# APR = annualized net entry credit / that collateral book's equity.
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
OPEN_MAX_LOSS_HALT_RATIO=0.40

# --- Position management ---
TP_CAPTURE_PCT=0.55
ENABLE_EARLY_EXIT=true
EARLY_EXIT_REMAINING_APR=0.08
EARLY_EXIT_MIN_PROFIT_CAPTURE=0.45
EARLY_EXIT_MAX_SPREAD_RATIO=0.06
TIME_EXIT_DTE=4
SOFT_DEFENSE_LOSS_PCT=0.30
HARD_STOP_LOSS_PCT=0.45

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
MAX_CONCURRENT_GROUPS=9
MAX_GROUPS_PER_BOOK=3
MAX_GROUPS_PER_CURRENCY=3
ENTRY_COOLDOWN_MINUTES=20
COOLDOWN_HOURS=12
RECOVERY_NORMAL_CYCLES=3
ENABLE_NAKED_TOPUP=false
ENABLE_ADOPT_EXCHANGE_POSITIONS=true

# --- Execution / state ---
OPTION_FEE_RATE=0.0003
OPTION_FEE_CAP_RATE=0.125
EXIT_BUFFER_RATIO=0.03
SHORT_ENTRY_WAIT_SECONDS=120
ORDER_POLL_SECONDS=10
POLL_SECONDS_NORMAL=15
POLL_SECONDS_STRESS=5
ORDER_LABEL_PREFIX=naked_short
REQUEST_TIMEOUT_SECONDS=20
STATE_FILE=.state/naked_short.json
```

策略專屬值請放在對應 profile 檔；切換策略時改 account env 的 `OPTION_STRATEGY`，並同步使用該策略自己的 `STATE_FILE`。

建議的 state 分流：

```text
covered_call:     STATE_FILE=.state/covered_call.json      ORDER_LABEL_PREFIX=covered_call
naked_short:      STATE_FILE=.state/naked_short.json       ORDER_LABEL_PREFIX=naked_short
bull_put_spread:  STATE_FILE=.state/bull_put_spread.json   ORDER_LABEL_PREFIX=bull_put_spread
```

下列三組內容已分別整理到 `.env.naked_short`、`.env.bull_put_spread`、`.env.covered_call`。

`naked_short`：尾端風險最大，但收益性排在 covered call 之後、bull put spread 之前。`SHORT_OPTION_SIDE` 可選 `put` / `call` / `both`：選 `both` 時 put 與 call 候選會用同一個 sort key 一起排序、競爭 `TOP_N` 名額。

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

`bull_put_spread`：有 long put 保護腿，但雙腿 debit 會犧牲淨 credit，所以 APR 門檻最低。

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

`covered_call`：只使用既有 BTC/ETH 現貨 cover，建議只開 inverse native book；這是最積極的收益 profile。

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

# Optional ITM spot exit; disabled by default.
COVERED_CALL_SPOT_EXIT_ENABLED=false
COVERED_CALL_ROBUST_EXIT_ENABLED=false
COVERED_CALL_ROBUST_EXIT_DTE=0.5
COVERED_CALL_ITM_BUFFER_PCT=0
COVERED_CALL_SPOT_ORDER_TYPE=market

MAX_GROUPS_PER_BOOK=3
MAX_GROUPS_PER_CURRENCY=3
```

注意：

- `ping` 可以不帶私有憑證
- `status`、`enter-best --live`、`manage --live`、`run --live`、`panic-close --live`、`cancel` 需要私有憑證
- Deribit 衍生品交易是否可用，取決於你的帳戶資格與司法管轄限制

## Commands

```bash
./bot ping
./bot scan --currencies BTC,ETH --json
./bot scan --strategy covered_call --currencies BTC,ETH --json
./bot enter-best --currencies BTC --json
./bot enter-best --currencies BTC --live --json
./bot manage --json
./bot manage --live --json
./bot run --cycles 1 --json
./bot run --cycles 0 --live
./bot panic-close --json
./bot panic-close --live --json
./bot status --json
./bot report --days 30 --json
./bot cancel --order-id YOUR_ORDER_ID --json
```

`scan --strategy` 可在不修改 `.env` 的情況下覆蓋本次掃描策略，並會套用同目錄對應的 `.env.<strategy>` profile。可用值為 `naked_short`、`bull_put_spread`、`covered_call`（舊名 `naked_short_put` / `naked_short_call` 仍會被接受並對應到 `naked_short`）。

一次啟動多個 live 設定檔：

```bash
python scripts/run_live_profiles.py .env.covered_call_sub .env.naked_short_sub .env.bull_put_spread_sub
```

不傳 env files 時，預設會跑上述三個 `*_sub` profile。每個 profile 會獨立執行 `run --cycles 0 --live`，log 寫到 `logs/live/<profile>.log`；按 `Ctrl-C` 會一起停止所有 live 程序。若想先跑有限輪數，可加 `--cycles 1`。

## Design Notes

- 認證為了試用簡化，HTTP private request 直接走 Basic Auth
- 掃描同時支援 `quote_currency=settlement_currency=USDC` 的線性 options，以及 `quote_currency=settlement_currency=BTC/ETH` 的 reversed options
- `portfolio APR` 用 `annualized net pnl / REFERENCE_CAPITAL_USDC`
- 已平倉表的 **`Annualized`**：`(realized_pnl / 該本位總 IM) × (365 / holding days)`。`realized_pnl` 仍為 USDC 等價；分母為 `estimated_im_collateral`（舊 state 無該欄時用 `max_loss` 與指數回推 IM）。USDC 本位直接相除；BTC／ETH 本位先把 PnL 除以標的 USD 指數換成幣，再除以 IM（幣）
- **`Return / max-loss`** 仍為 `realized_pnl / max_loss`（與上列年化分母口徑不同時，兩欄數字不必一致）
- 所有 `credit / debit / max loss / report` 內部都統一換算成 `USDC equivalent`
- 本地狀態保存在 `STATE_FILE`；多策略 / 多子帳戶時請使用 `.state/covered_call.json`、`.state/naked_short.json`、`.state/bull_put_spread.json` 等獨立檔案
- `report` 讀本地 state 中已關閉 spread 的 realized 資料；若啟用 perp hedge，報表仍只統計 spread PnL，不含 perp hedge PnL
- `run` 會先做 `manage`，再在條件允許時嘗試 `enter-best`

## Local Dashboard

`./bot frontend` 會啟一個本地 FastAPI server + 純 HTML 單頁前端，把 `status` /
`report` / `stress-current` / `STATE_FILE` 整合成一頁式 dashboard，
明確區分 **BTC / ETH / USDC** 三本位帳戶（由 `PortfolioSnapshot` 內建分桶），顯示：

- 三張 book card：USDC-equivalent equity、原幣 equity、day P&L、IM/MM ratio、delta、regime
- 投組總覽 USDC card：total equity、total profit（lifetime + 30d window）、lifetime/window APR、win rate、avg holding days
- 圖表：open max loss vs book equity（USDC）、累積 realized PnL、每日 PnL + 30d MA、rolling APR
- Open spreads 表 / Recent closed trades 表
- 黑天鵝壓力測試卡（同 `./bot stress-current`，會依 `OPTION_STRATEGY` 顯示 `naked_short` / `bull_put_spread` / `covered_call` 的風險解讀）

啟動：

```bash
pip install -r requirements.txt
./bot frontend                       # 預設 http://127.0.0.1:8765
./bot frontend --port 9000           # 換埠
./bot frontend --no-scheduler        # 關掉背景 equity ledger
./bot frontend --account-env-files .env,.env.naked_short_sub,.env.bull_put_spread_sub
# 若 covered call 也改用本機帳戶 env：
# ./bot frontend --account-env-files .env.covered_call_sub,.env.naked_short_sub,.env.bull_put_spread_sub
```

預設背景 scheduler 每 `FRONTEND_SNAPSHOT_INTERVAL_SEC` 秒（預設 300）讀一次帳戶
快照，append 到 `data/frontend_ledger/equity_<UTC date>.jsonl`（可給自備分析／之後圖表用）。
沒設 `DERIBIT_CLIENT_ID/SECRET` 時 scheduler 自動跳過，但 server
依然可看 closed groups / 累積 PnL / APR 圖。
前端頁面資料刷新有 30 秒節流上限；自動刷新與手動 `Refresh` 都會套用同一個限制。

多子帳戶 dashboard 可用 `--account-env-files` 傳入多個策略 env。後端會保留同一個單頁前端格式，聚合各子帳戶的 `status`、`report`、open groups、closed trades、APR 與 stress；每個子帳戶仍需使用自己的 `DERIBIT_CLIENT_ID/SECRET`、`STATE_FILE` 與 `ORDER_LABEL_PREFIX`。主帳號若維持 covered call，也可以用 `.env,.env.naked_short_sub,.env.bull_put_spread_sub` 啟動。多帳戶模式下 equity ledger 會依 account name 分別寫入 `data/frontend_ledger/<account>/`，避免不同子帳戶快照混在同一個檔案。

## Tests

```bash
pytest
```
