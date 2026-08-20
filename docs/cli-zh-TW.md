# CLI 指令

## 列出所有指令

```bash
./bot help              # 分類列出全部頂層指令
./bot help investor     # 列出 investor 子指令
./bot                 # 同 ./bot help（無參數時印目錄）
./bot <command> -h      # 單一指令的完整參數說明
```

## 怎麼指定用哪個子帳

| 方式 | 範例 |
|------|------|
| **投資人 + slug**（建議） | `export INVESTOR=youming` 後 `./bot --investor $INVESTOR --account naked <子命令>`；slug 見 `config/investors/<id>/accounts.toml` |
| **直接 env 路徑** | `./bot --env-file config/investors/youming/accounts/.env.naked <子命令>`（路徑可寫在子命令前或後） |
| **舊版單一 `.env`**（不建議） | 不帶 `--investor`，預設讀 repo 根目錄 `.env` |

`--investor` 與一般子命令並用時，**多數子命令必須加 `--account <slug>`**（`frontend` 例外：不帶 `--account` 時會聚合該投資人 `accounts.toml` 內所有 `enabled` 子帳）。

- 預設 **dry-run**；要真的下單須加 `--live`（`enter-best`、`manage`、`run`、`panic-close`、`close-position`）。
- 除 `ping` 外，需要連線與私有金鑰；實單前先在 dry-run 確認輸出。

## 投資人子帳（youming 範例）

```bash
export INVESTOR=youming
ACCT=naked   # 或 bull_put、covered_call，見 accounts.toml

# 連線 / 部位 / 掃描 / 一輪管理（dry-run）
./bot --investor $INVESTOR --account $ACCT ping --json
./bot --investor $INVESTOR --account $ACCT status --json
./bot --investor $INVESTOR --account $ACCT scan --currencies BTC,ETH --json
./bot --investor $INVESTOR --account $ACCT manage --json

# 下單與持續迴圈（--live 才實單）
./bot --investor $INVESTOR --account $ACCT enter-best --currencies BTC,ETH --json
./bot --investor $INVESTOR --account $ACCT enter-best --currencies BTC,ETH --live --json
./bot --investor $INVESTOR --account $ACCT manage --live --json
./bot --investor $INVESTOR --account $ACCT run --cycles 1 --json
./bot --investor $INVESTOR --account $ACCT run --cycles 0 --live

# 報表、壓力測試、成交查詢（依子帳 API）
./bot --investor $INVESTOR --account $ACCT report --days 30 --json
./bot --investor $INVESTOR --account $ACCT stress-current --json
./bot --investor $INVESTOR --account $ACCT user-trades --currency USDC --count 50 --json

# 緊急全平（取消掛單 + 平倉；--live 才送單）
./bot --investor $INVESTOR --account $ACCT panic-close --json
./bot --investor $INVESTOR --account $ACCT panic-close --live --json

# 依 order id 取消單筆掛單
./bot --investor $INVESTOR --account $ACCT cancel --order-id YOUR_ORDER_ID --json
```

## 儀表板與多子帳 live

```bash
export INVESTOR=youming

# 本地 dashboard（預設 http://127.0.0.1:8765 ）
./bot --investor $INVESTOR frontend
./bot --investor $INVESTOR frontend --port 9000
./bot frontend --account-env-files config/investors/$INVESTOR/accounts/.env.naked,config/investors/$INVESTOR/accounts/.env.bull_put

# macOS launchd 常駐（依 registry.toml）
./bot investor frontend start    # dashboard
./bot investor tunnel start      # cloudflared tunnel run
./bot investor provision-tunnel  # sync local+remote ingress + DNS from registry
./bot investor live start        # 實單監督

# 同時啟動 accounts.toml 內 live_enabled 子帳的 `run --live`（log：logs/live/<investor_id>/<slug>.log）
python scripts/run_live_profiles.py --investor $INVESTOR --restart-failed

# 不經 --investor，改用手動列出多個子帳 env：
python scripts/run_live_profiles.py \
  config/investors/$INVESTOR/accounts/.env.naked \
  config/investors/$INVESTOR/accounts/.env.bull_put
```

Dashboard 詳細說明見 [本地 Dashboard](dashboard-zh-TW.md)。Tunnel 手動 `run` 與對外設定見 [cloudflare-tunnel-investor.md](cloudflare-tunnel-investor.md)。

## 舊版單一 `.env`（legacy，不建議）

若尚未遷移到 `config/investors/...`，仍可用 repo 根目錄 `.env`：

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

（也可用為除錯路徑單獨指定：`./bot --env-file ./.env scan --json`。）

`scan --strategy` 可在不修改 `.env` 的情況下覆蓋本次掃描策略，並會套用同目錄對應的 `.env.<strategy>` profile。可用值為 `naked_short`、`bull_put_spread`、`covered_call`（舊名 `naked_short_put` / `naked_short_call` 仍會被接受並對應到 `naked_short`）。

## 歷史回測（research only）

使用 Deribit 公開行情做離線回測；報告預設寫入 `docs/backtest/`（不影響 live state）。

```bash
./bot --env-file config/investors/_example/accounts/.env.naked.example backtest \
  --start 2024-01-01 --end today --json
```

更多報告範例見 [`backtest/`](backtest/)。

## `close-position`（子帳精準平倉）

關閉**指定合約**的交易所倉位，適合手動殘倉、單腿調整或只平某一張期權／永續。與 `panic-close` 不同：不會取消全部掛單、不平掉其他 group、不寫入 portfolio cooldown。

**請用子帳 env**（API key 已限定該子帳），例如 `config/investors/youming/accounts/.env.naked`，或 `./bot --investor youming --account naked`。

| 參數 | 說明 |
|------|------|
| `--env-file PATH` | 子帳憑證與 `STATE_FILE`（可寫在子命令前或後） |
| `--list` | 只列出非零倉位（dry-run，不需 `--instrument`／`--group-id`） |
| `--instrument NAME` | 要平的合約全名；可重複傳入或逗號分隔多個 |
| `--group-id ID` | 依本地 open trade group 平倉（走 `_close_group`／`manual_close`）；可重複或逗號分隔 |
| `--live` | 實際送單；省略則僅預覽 |
| `--order-type market\|limit` | 預設 `market`；選擇權 `limit` 走 IOC limit + retry（同 `manage` 平倉；**`--group-id` 忽略此參數**） |
| `--amount QTY` | 部分平倉張數；省略則平掉該合約全部倉位（**不可與 `--group-id` 併用**） |
| `--json` | JSON 輸出 |

平倉方式（依合約類型）：

- **`--instrument`**：選擇權 `market` → reduce-only 市價單；`limit` → reduce-only IOC limit（含 retry）；永續／期貨 → `private/close_position`（市價）。此路徑**不**自動更新本地 group。
- **`--group-id`**：依 state 內 open group 用策略平倉路徑（short／long 腿、寫入 `close_reason=manual_close`）；dry-run 回傳 `actions[].action = close_group_preview`。

```bash
export INVESTOR=youming

# 1) 先看子帳有哪些倉位 / group
./bot --investor $INVESTOR --account naked status --json
./bot --investor $INVESTOR --account naked close-position --list --json

# 2) 預覽平某一張（不送單）
./bot --investor $INVESTOR --account naked close-position \
  --instrument BTC_USDC-27MAR26-90000-P --json

# 3) 依 group id 預覽提前關倉（不送單）
./bot --investor $INVESTOR --account naked close-position \
  --group-id 0001 --json

# 4) 市價全平該合約
./bot --investor $INVESTOR --account naked close-position \
  --instrument BTC_USDC-27MAR26-90000-P --live --json

# 5) 依 group id 實單平倉
./bot --investor $INVESTOR --account naked close-position \
  --group-id 0001 --live --json

# 6) 選擇權用 limit 平倉（僅 --instrument）
./bot --investor $INVESTOR --account bull_put close-position \
  --instrument BTC_USDC-27MAR26-88000-P --order-type limit --live --json
```

### 與 `panic-close` 對照

| | `close-position --instrument` | `close-position --group-id` | `panic-close` |
|--|------------------------------|-----------------------------|---------------|
| 範圍 | 指定合約 | 指定 open group | 全部 open group + PERP |
| 掛單 | 不取消 | 不取消其他掛單 | 取消所有 open orders |
| Cooldown | 不設定 | 不設定 | 寫入全 book cooldown |
| 本地 state | 不自動更新 group | 標記該 group 為 closed | 標記全部 closed |

用 `--instrument` 手動平掉 bot 有追蹤的 spread 後，本地 `STATE_FILE` 可能與交易所不一致；之後可再跑 `manage` 讓 reconcile 收斂。`--group-id` 路徑會直接更新該 group。

## Covered call profit sweep

啟用 `COVERED_CALL_PROFIT_SWEEP_ENABLED=true` 後，live `manage` 會在獲利平倉時自動將 premium 賣成 USDT。設定與滑價保護見 [`configuration-zh-TW.md`](configuration-zh-TW.md#績效費-nav-快照performance-fee)。

```bash
export INVESTOR=youming
ACCT=covered_call

# 預覽剩餘 spot profit sweep（dry-run）
./bot --investor $INVESTOR --account $ACCT profit-sweep --json

# 實單 sweep 全部 pending remainder
./bot --investor $INVESTOR --account $ACCT profit-sweep --live --json

# 只 sweep 單一已平倉 group
./bot --investor $INVESTOR --account $ACCT profit-sweep --group-id cc-btc-20260327 --live --json

# 只從 Deribit order label 同步 profit_sweep_*，不下單
./bot --investor $INVESTOR --account $ACCT profit-sweep --reconcile-only --json
```

| 參數 | 說明 |
|------|------|
| `--group-id` | 只處理指定已平倉 group |
| `--reconcile-only` | 只同步 state 上的 sweep 欄位，不送 spot 單 |
| `--live` | 實際下單並寫入 state（預設 dry-run） |

亦可使用等價腳本 `python scripts/sweep_remaining_spot_profit.py --env-file config/investors/$INVESTOR/accounts/.env.$ACCT`。

### 修復 / 回填（ops）

| 情境 | 指令 |
|------|------|
| 各 group proceeds 與交易所 net sweep 不一致 | `python scripts/reconcile_premium_proceeds.py --env-file ... [--live]` |
| Premium 賣超或賣不足 | `python scripts/align_premium_swap.py --env-file ... [--live]` |
| 重複 sweep、需買回多賣原幣 | `python scripts/repair_double_profit_sweep.py --env-file ... [--live]` |
| Ledger 缺 `equity_native_by_book` | `python scripts/backfill_ledger_equity_native.py --investor <id>` |

詳見 [`scripts/README.md`](../scripts/README.md)。

## Covered call ITM spot restore（手動買回 cover）

ITM / settlement spot exit 賣掉 cover（`cover − settle`；權利金另走 Profit swap）後，若要用 USDT 買回現貨並正確記帳，請用 `spot-restore`（**不要**用裸 `trade-spot`，也**不要**用 `profit-sweep-buyback` label）。

```bash
export INVESTOR=youming
ACCT=covered_call

# 預覽（預設人讀格式：預計買回 / 當前價格 / 預計花費 USDT）
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017

# 同上，輸出完整 JSON（含 preview.buy_amount / current_price_usdt / estimated_usdt）
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017 --json

# 買回單一 group 至完整 cover（預設 = swap + settle + fee − 已買回）
# 預設 limit@bid GTC post_only，掛單等待 SPOT_RESTORE_WAIT_SECONDS（預設 120s）後取消未成交
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017 --live

# 只買回部分 native 數量
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017 --amount 0.05 --live --json

# 指定花費 USDT 數量買回（與 --amount 互斥；超出尚未補滿的 cover 會自動封頂）
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017 --usdt 4500 --live --json
# 等價：--quote 4500

# 改掛單等待時間 / 改用市價
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017 --wait-seconds 180 --live
./bot --investor $INVESTOR --account $ACCT spot-restore --group-id 0017 --order-type market --live

# 只從 Deribit `*-spot-restore` label 同步 spot_restore_*，不下單
./bot --investor $INVESTOR --account $ACCT spot-restore --reconcile-only --json
```

| 參數 | 說明 |
|------|------|
| `--group-id` | 只處理指定已平倉 group |
| `--amount` | 買回 native 數量；**預設**為補滿原始 cover：`swap（spot exit）+ settle + fee − 已 restore` |
| `--usdt` / `--quote` | 花費 USDT 買回（與 `--amount` 互斥）；超過尚未補滿的 cover 時會封頂 |
| `--order-type` | `limit`（預設）或 `market`；亦可設 `SPOT_RESTORE_ORDER_TYPE` |
| `--wait-seconds` | limit 掛單等待秒數後取消未成交（預設 `SPOT_RESTORE_WAIT_SECONDS=120`） |
| `--reconcile-only` | 只同步 state 上的 restore 欄位，不送 spot 單 |
| `--live` | 實際下單並寫入 state（預設 dry-run） |

預設數量拆解（preview JSON 的 `buy_amount_composition` / `preview`）：

- **swap_sold** = **實際已成交**的 ITM spot exit（含 partial / pending）
- **settlement_loss** = 結算損失（缺則回讀 transaction log / intrinsic；**務必有值**）
- **premium_native** = 淨進場權利金（fill − entry fee）。若 ITM 沒把 premium 賣進 spot exit，現貨會多這筆 → restore **要減掉**，否則會買成 cover+premium
- **buy_amount** = `min(cover, swap + settle − premium) − already_restored`
- **estimated_usdt** = `buy_amount × current_price`（limit 用 best bid；market 用 best ask）
- **order_budget_usdt** = limit 時同 estimated；market 時 `buy × ask × 1.005`（緩衝）
- **limit 預設**：post-only buy@bid GTC，等待 `--wait-seconds`／`SPOT_RESTORE_WAIT_SECONDS` 後取消未成交；可 `--order-type market` 改市價
- **低於交易所最小下單量**（例如 ETH_USDT min `0.001`）：**省略**（`dust_below_min`），**不進位**；`--live` 會標記 restore 完成，避免為塵埃多買超過 cover

ITM + `COVERED_CALL_PROFIT_SWEEP_ENABLED` 卻只賣出 cover−settle 時：restore 回到 **cover**，錢包留下的 premium 是 spot profit（之後可再 `profit-sweep` 兌 USDT）。

記帳：

- 訂單 label：`{short_label}-spot-restore`
- Journal：`spot_restore_amount` / `spot_restore_quote_spent`（與 Profit swap 分離）
- Dashboard **ITM spot exit** 區塊顯示 Sold / Bought back / 淨 USDT
- Restore 完成後，`exit USDT − restore USDT` 會計入 **Total profit**（績效費基數）；未買回 cover 前不把整筆 spot 賣出當獲利
