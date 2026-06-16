# 快速開始

## 環境需求

- Python 3.11+（使用 `datetime.UTC`）

## 安裝

```bash
cd deribit-options-strategy-engine
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

僅部署 bot / frontend 時可只裝 `pip install -r requirements.txt`。

## 建立投資人設定

設定放在 `config/investors/<id>/`，子帳憑證在 `accounts/.env.<slug>`（見 [`config/investors/_example/`](../config/investors/_example/)）。**不要**再用 repo 根目錄單一 `.env`。

```bash
# 手動複製範本，或使用 CLI：
# ./bot investor init youming --strategies naked,bull_put_spread
cp -R config/investors/_example config/investors/youming

# 依 accounts/.env.<slug>.example 建立子帳 env 並填入 API key
cp config/investors/youming/accounts/.env.naked.example \
   config/investors/youming/accounts/.env.naked
# 編輯 .env.naked：DERIBIT_CLIENT_ID、DERIBIT_CLIENT_SECRET 等

export INVESTOR=youming
```

## 開發：測試與 lint

```bash
pytest tests/ -q
ruff check deribit_engine tests scripts
ruff format --check deribit_engine tests scripts
```

## 最小設定

每個子帳 `accounts/.env.<slug>` 至少要設定：

- `DERIBIT_ENV=mainnet`
- `DERIBIT_CLIENT_ID`
- `DERIBIT_CLIENT_SECRET`
- `STATE_FILE`、`REFERENCE_CAPITAL_USDC` 等（見 `accounts/.env.<slug>.example`）

策略種類與風險分級在 **`accounts.toml`** 指定（`strategy` = `naked_short` / `bull_put_spread` / `covered_call`；`risk_tier` 預設 `medium`），**不要**在子帳 env 重複填 `OPTION_STRATEGY` 或 `RISK_TIER`。

投資人層級費率等可選設定見 `config/investors/<id>/.env.investor`；共用 fallback 見 `config/shared/.env.defaults`（含 `ENABLE_IV_ENTRY_GATE=true` 等 pacing 預設）。其餘參數見 [設定與環境變數](configuration-zh-TW.md)。

## 第一次執行

```bash
export INVESTOR=youming

# 連線測試（可不帶私有憑證）
./bot --investor $INVESTOR --account naked ping

# 掃描候選（dry-run，預設行為）
./bot --investor $INVESTOR --account naked scan --currencies BTC,ETH --json
```

更多子命令見 [CLI 指令](cli-zh-TW.md)。

## 測試

```bash
pytest
```

## 相關文件

- [策略說明](strategies-zh-TW.md)
- [設定與環境變數](configuration-zh-TW.md)
- [CLI 指令](cli-zh-TW.md)
- [本地 Dashboard](dashboard-zh-TW.md)
