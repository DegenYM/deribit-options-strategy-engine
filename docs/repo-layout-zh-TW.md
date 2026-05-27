# Repository 目錄架構

本文件說明 **canonical（現行）** 目錄配置，以及仍可相容但應逐步淘汰的 **legacy** 路徑。

## 總覽

```text
deribit-options-strategy-engine/
├── bot                          # CLI 入口（→ deribit_engine.cli）
├── deribit_engine/                # 策略引擎、frontend、投資人 ops
├── config/
│   ├── shared/                  # 全投資人共用（無 API 金鑰）
│   │   ├── defaults.env.example
│   │   └── strategies/.env.*    # 策略 tuning（納版、可追蹤）
│   ├── investors/
│   │   ├── _example/            # 範本（納版）
│   │   └── <investor_id>/       # 本機投資人設定（gitignore，小寫 id）
│   ├── platform/
│   │   ├── registry.toml.example
│   │   ├── registry.toml        # 本機 ops 中繼資料（gitignore）
│   │   └── generated/           # investor init 產物（gitignore）
│   │       ├── launchd/
│   │       └── systemd/
│   ├── launchd/
│   │   ├── com.deribit.live.plist.template
│   │   └── com.deribit.frontend.plist.template
│   ├── systemd/
│   │   ├── com.deribit.live.service.template
│   │   └── com.deribit.frontend.service.template
│   └── handoff/
│       └── handoff.template.toml
├── frontend/                    # Dashboard 靜態頁（src/ ES modules → app.js bundle）
├── scripts/                     # live 監督、PDF 產生、fee 快照等
├── tests/
├── docs/                        # 操作手冊、披露、backtest 報告
├── data/                        # 執行期資料（gitignore）
├── logs/                        # 執行期日誌（gitignore）
├── .state/                      # 策略 state（gitignore）
└── output/pdf/                  # 對外 PDF 交付物（部分納版）
```

## 設定載入（現行）

| 層級 | 路徑 | 用途 |
|------|------|------|
| 1 | `config/shared/defaults.env` | 可選共用 fallback（`config/shared/.env.defaults` 為 legacy 別名，載入時會提示） |
| 2 | `config/investors/<id>/.env.investor` | 投資人層級（費率、備兌現貨等） |
| 3 | `config/shared/strategies/.env.<strategy>` | 策略參數 |
| 4 | `config/investors/<id>/accounts/.env.<slug>` | 子帳憑證、資金規模、覆寫 |
| — | `config/investors/<id>/accounts/.env.fee` | 費用專戶（`ACCOUNT_ROLE=fee`；**不在** accounts.toml） |

子帳清單：`config/investors/<id>/accounts.toml`（`[investor].id` 必須 **小寫**）。**fee 專戶不寫入 manifest**。

## 執行期資料（現行）

每位投資人 `<investor_id>`（小寫）各自隔離：

| 用途 | 路徑 |
|------|------|
| 策略 state | `.state/investors/<id>/<slug>.json` |
| trade journal | `.state/investors/<id>/<slug>.trade_journal.db` |
| Dashboard ledger | `data/frontend_ledger/<id>/[<slug>/]equity_*.jsonl` |
| Dashboard metrics | `data/frontend_ledger/<id>/metrics.db` |
| Live 日誌 | `logs/live/<id>/<slug>.log` |
| Frontend 日誌 | `logs/frontend/<id>/` |
| 績效費快照 | `data/fee_ledger/<id>/snapshots.db` |
| 費用報表 | `data/fee_ledger/<id>/reports/` |

## launchd / systemd

- **範本**：`config/launchd/*.plist.template`、`config/systemd/*.service.template`（佔位符 `__REPO_ROOT__` 等）
- **已填入路徑的產物**：`./bot investor init` → `config/platform/generated/launchd/`、`generated/systemd/`
- **macOS 安裝**：複製 generated plist 到 `~/Library/LaunchAgents/`，或 `./bot investor live|frontend start`
- **Linux 安裝**：複製 generated unit 到 `/etc/systemd/system/`，見 [`live-profiles-systemd-zh-TW.md`](live-profiles-systemd-zh-TW.md)

不再維護 per-investor 的 `com.deribit.*.jack.plist.example` 等重複檔案。

## Legacy（應淘汰）

以下為早期「單一 `.env` + flat 目錄」時期的產物，程式仍相容，但新部署請勿使用：

| Legacy | 現行替代 |
|--------|----------|
| repo 根目錄 `.env.<strategy>` | `config/shared/strategies/.env.<strategy>` |
| `.env.<strategy>_sub` | `config/investors/<id>/accounts/.env.<slug>` |
| `.state/<strategy>.json` | `.state/investors/<id>/<slug>.json` |
| `data/frontend_ledger/<strategy>/` | `data/frontend_ledger/<id>/<slug>/` |
| `data/frontend_ledger/metrics.db`（根目錄） | `data/frontend_ledger/<id>/metrics.db` |
| `data/frontend_ledger/An/`（大小寫不一致） | `data/frontend_ledger/an/` |
| `accounts/<slug>.env` | `accounts/.env.<slug>` |
| `investor.env` | `.env.investor` |
| `reports/*.md`（根目錄） | `docs/backtest/*.md` |

### 本機清理

```bash
# 預覽
./scripts/cleanup_legacy_layout.sh --dry-run

# 執行（合併 An→an、移除 flat ledger 等）
./scripts/cleanup_legacy_layout.sh
```

### 投資人設定正規化（P2 checklist）

新部署或遷移後，確認每位 `<investor_id>`：

| 檢查項 | Canonical |
|--------|-----------|
| 投資人 env | `config/investors/<id>/.env.investor`（非 `investor.env`） |
| 子帳 env | `accounts/.env.<slug>`（非 `accounts/<slug>.env`） |
| `STATE_FILE` | `.state/investors/<id>/<slug>.json` |
| manifest slug | 與 `.state/investors/<id>/` 檔名一致 |
| 多子帳 ledger | `data/frontend_ledger/<id>/<slug>/equity_*.jsonl` |

```bash
./bot investor validate <investor_id>
```

## 納版 vs 本機

| 納版（git） | 本機專用（gitignore） |
|-------------|----------------------|
| `config/investors/_example/` | `config/investors/<id>/` |
| `config/shared/strategies/.env.*` | `config/shared/defaults.env` |
| `config/platform/registry.toml.example` | `config/platform/registry.toml` |
| `config/handoff/handoff.template.toml` | `config/handoff/<id>.toml` |
| `docs/`、`tests/`、`deribit_engine/` | `.state/`、`data/`、`logs/` |

## 腳本分類

| 目錄 | 用途 |
|------|------|
| `scripts/` | Production / ops：live 監督、fee 快照、PDF、E2E、legacy 清理 |
| `scripts/dev/` | 一次性或開發輔助：dashboard 模組化、state 回填、手動 boot 測試 |

## 相關文件

- 投資人設定：[`configuration-zh-TW.md`](configuration-zh-TW.md) → Investor / Sub-account Layout
- 管理方 onboarding：[`operator-onboarding-zh-TW.md`](operator-onboarding-zh-TW.md)
- Live launchd：[`live-profiles-launchd-zh-TW.md`](live-profiles-launchd-zh-TW.md)
- Live systemd（Linux）：[`live-profiles-systemd-zh-TW.md`](live-profiles-systemd-zh-TW.md)
