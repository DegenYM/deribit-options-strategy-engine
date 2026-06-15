# CLI 指令

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
| `--list` | 只列出非零倉位（dry-run，不需 `--instrument`） |
| `--instrument NAME` | 要平的合約全名；可重複傳入或逗號分隔多個 |
| `--live` | 實際送單；省略則僅預覽 |
| `--order-type market\|limit` | 預設 `market`；選擇權 `limit` 走 IOC limit + retry（同 `manage` 平倉） |
| `--amount QTY` | 部分平倉張數；省略則平掉該合約全部倉位 |
| `--json` | JSON 輸出 |

平倉方式（依合約類型）：

- **選擇權**：`market` → reduce-only 市價單；`limit` → reduce-only IOC limit（含 retry）
- **永續／期貨**：`private/close_position`（市價）

```bash
export INVESTOR=youming

# 1) 先看子帳有哪些倉位
./bot --investor $INVESTOR --account naked status --json
./bot --investor $INVESTOR --account naked close-position --list --json

# 2) 預覽平某一張（不送單）
./bot --investor $INVESTOR --account naked close-position \
  --instrument BTC_USDC-27MAR26-90000-P --json

# 3) 市價全平該合約
./bot --investor $INVESTOR --account naked close-position \
  --instrument BTC_USDC-27MAR26-90000-P --live --json

# 4) 選擇權用 limit 平倉
./bot --investor $INVESTOR --account bull_put close-position \
  --instrument BTC_USDC-27MAR26-88000-P --order-type limit --live --json
```

### 與 `panic-close` 對照

| | `close-position` | `panic-close` |
|--|------------------|---------------|
| 範圍 | 僅 `--instrument` 指定合約 | 全部 open group + PERP |
| 掛單 | 不取消 | 取消所有 open orders |
| Cooldown | 不設定 | 寫入全 book cooldown |
| 本地 state | 不自動更新 group | 標記 group 為 closed |

手動平掉 bot 有追蹤的 spread 後，本地 `STATE_FILE` 可能與交易所不一致；之後可再跑 `manage` 讓 reconcile 收斂。

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
