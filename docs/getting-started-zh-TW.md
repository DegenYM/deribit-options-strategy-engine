# 快速開始

## 環境需求

- Python 3.11+（使用 `datetime.UTC`）

## 安裝

```bash
cd deribit-options-strategy-engine
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

僅部署 bot / frontend 時可只裝 `pip install -r requirements.txt`。

## 開發：測試與 lint

```bash
pytest tests/ -q
ruff check deribit_engine tests scripts
ruff format --check deribit_engine tests scripts
```

## 最小設定

至少要設定：

- `DERIBIT_ENV=testnet` 或 `mainnet`
- `DERIBIT_CLIENT_ID`
- `DERIBIT_CLIENT_SECRET`
- `OPTION_STRATEGY`：`naked_short`、`bull_put_spread` 或 `covered_call`
- `ENABLE_PERP_HEDGE=false`（預設即關閉）

其餘參數見 [`.env.example`](../.env.example) 與 [設定與環境變數](configuration-zh-TW.md)。

## 第一次執行

```bash
# 連線測試（可不帶私有憑證）
./bot ping

# 掃描候選（dry-run，預設行為）
./bot scan --currencies BTC,ETH --json
```

使用投資人子帳 layout 時，見 [CLI 指令](cli-zh-TW.md)。

## 測試

```bash
pytest
```

## 相關文件

- [策略說明](strategies-zh-TW.md)
- [設定與環境變數](configuration-zh-TW.md)
- [CLI 指令](cli-zh-TW.md)
- [本地 Dashboard](dashboard-zh-TW.md)
