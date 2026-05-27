# Deribit Options Strategy Engine

`BTC + ETH` 的 Deribit 自動化 option 策略引擎，可透過 `OPTION_STRATEGY` 選擇策略。

GitHub: https://github.com/DegenYM/deribit-options-strategy-engine

This project is not affiliated with or endorsed by Deribit.

## 快速開始

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
./bot ping
./bot scan --currencies BTC,ETH --json   # 預設 dry-run
```

完整安裝、測試與第一次執行：[`docs/getting-started-zh-TW.md`](docs/getting-started-zh-TW.md)

## 策略概覽

| 策略 | 說明 |
|------|------|
| `naked_short` | 單腿 short option（`put` / `call` / `both`） |
| `bull_put_spread` | 賣 short put + 買 long put 保護腿 |
| `covered_call` | 用既有 BTC/ETH 現貨 cover 賣 call |

- 可選 `perp` delta hedge；`spot` 僅供異常庫存處理
- 目標：`1000 USDC` 參考資金下年化淨利 `200 USDC+`
- 預設 **dry-run**；實單須加 `--live`

詳細模型、風控與 payoff 圖：[`docs/strategies-zh-TW.md`](docs/strategies-zh-TW.md)

## 文件

### 使用與設定

| 文件 | 內容 |
|------|------|
| [快速開始](docs/getting-started-zh-TW.md) | 安裝、測試、最小設定 |
| [策略說明](docs/strategies-zh-TW.md) | 策略模型、風控、payoff 示意 |
| [設定與環境變數](docs/configuration-zh-TW.md) | 投資人 layout、env 範例、績效費快照 |
| [CLI 指令](docs/cli-zh-TW.md) | 子帳指定、常用命令、`close-position` |
| [本地 Dashboard](docs/dashboard-zh-TW.md) | 儀表板、多投資人、Tunnel / launchd |
| [設計備註](docs/design-notes-zh-TW.md) | APR 口徑、state、report 行為 |

### 投資人與營運

| 文件 | 內容 |
|------|------|
| [投資人 onboarding](docs/investor-onboarding-zh-TW.md) | 入金、子帳、API Key |
| [管理方 onboarding](docs/operator-onboarding-zh-TW.md) | `investor init`、registry |
| [目錄架構](docs/repo-layout-zh-TW.md) | canonical layout、legacy 遷移 |
| [績效費口徑](docs/investor-fee-disclosure-zh-TW.md) | NAV、HWM、計費 |
| [Telegram 告警](docs/telegram-alerts-zh-TW.md) | 告警設定 |
| [Live 故障 runbooks](docs/runbooks/README-zh-TW.md) | state 不一致、429、panic、Tunnel |
| [優化路線圖](docs/optimization-plan-zh-TW.md) | CI、營運、架構拆分 |

### 其他

| 文件 | 內容 |
|------|------|
| [回測報告](docs/backtest/) | 離線 research 範例 |
| [前端 build / e2e](frontend/README.md) | Dashboard 前端 |
| [腳本說明](scripts/README.md) | 輔助腳本 |

## 投資人 layout（建議）

一位投資人一個目錄，底下最多數個策略子帳；範本見 [`config/investors/_example/`](config/investors/_example/)。

```bash
cp -R config/investors/_example config/investors/youming
./bot --investor youming --account naked scan --currencies BTC,ETH --json
./bot --investor youming frontend
```

詳細載入順序與 env 範例：[`docs/configuration-zh-TW.md`](docs/configuration-zh-TW.md)
