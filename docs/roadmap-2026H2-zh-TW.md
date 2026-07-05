# 優化 Roadmap（2026 H2）

本文件基於 2026-07-05 的全專案健檢結果撰寫，接續 [`optimization-plan-zh-TW.md`](optimization-plan-zh-TW.md)（P0–P2、P4 已完成）。
健檢重點發現：

- P2「engine 各檔 < ~1500 行」**未達成**：`engine/base.py` 2232 行、`engine/management.py` 1927 行。
- 計畫外盲點：`strategy.py`（2427 行，全專案最大）、`models.py`（1838）、`trade_journal_backfill.py`（2051）、`client.py`（1152）、`frontend_server/app.py` 的 `create_app()` 單函式約 1036 行。
- 測試已成長至 614 項（文件仍寫 548）；本機 `.venv` 缺 `pytest-socket`，直接跑 pytest 會失敗。
- `.gitignore` 未涵蓋 `data/*.sqlite`；部分 SQLite Store 讀取未加 lock；6 個 Store 有 ~30 行重複的連線樣板。
- P3.2 Docker Compose、P3.3 Uptime 監控尚未實作。

---

## Wave 1 — 平行工作流（本輪派工）

四個工作流檔案範圍互不重疊，可同時進行。共同規範：**不 commit**、每個 PR 級改動需 Ruff 全綠 + 對應測試通過、對外 import 路徑不變。

### Workstream A — 快贏與 dev 環境對齊（S）

| 項目 | 內容 |
|------|------|
| 範圍 | `.gitignore`（補 `data/*.sqlite`）、`.pre-commit-config.yaml`（與 CI pytest 參數對齊）、`.venv` 安裝 `pytest-socket`、`tests/` 註冊 `enable_socket` mark、`docs/optimization-plan-zh-TW.md` 數據同步（614 tests、巨型檔清單） |
| 完成標準 | pre-commit 與 CI 行為一致；`pytest --collect-only` 無警告；文件與現況相符 |

### Workstream B — 部署與監控（P3.2 + P3.3）（M）

| 項目 | 內容 |
|------|------|
| 範圍 | 新增 `Dockerfile` + `docker-compose.yml`（bot + frontend，volume 掛 `config/`、`.state/`）；新增 `scripts/check_frontend_uptime.py`（HTTP 檢查 + Telegram 告警）；對應文件 |
| 完成標準 | 只新增檔案，不動既有程式；compose 可 build；uptime script 有單元測試 |

### Workstream C — 拆分 `frontend_server/app.py` 的 `create_app()`（M）

| 項目 | 內容 |
|------|------|
| 範圍 | 把 ~1036 行的 `create_app()` 拆為 route 註冊、scheduler 接線、cache 初始化等模組；順手收斂該檔 12 處寬鬆 `except Exception` |
| 完成標準 | `app.py` < ~400 行；`tests/test_frontend_server.py`、`tests/e2e/` 全綠；API 行為不變 |

### Workstream D — 拆分 `engine/management.py`（L）

| 項目 | 內容 |
|------|------|
| 範圍 | 1927 行 → 按 portfolio snapshot（`_build_portfolio_snapshot` 317 行）/ group refresh / exit 邏輯拆檔；不動 `engine/base.py`（留待 Wave 2） |
| 完成標準 | `management.py` < ~1000 行；`tests/test_engine.py` 等相關測試全綠；對外 import 不變 |

---

## Wave 2 — 串行接續（Wave 1 收斂後）

| 順序 | 項目 | 規模 | 備註 |
|------|------|------|------|
| 1 | 拆 `engine/base.py`（2232 行） | L | 與 Workstream D 共用 tests，須等 D 合併 |
| 2 | 拆 `strategy.py`（2427 行） | L | 按策略類型分模組 |
| 3 | SQLite Store 基底類 + 讀取 lock 一致性 | M | 抽 `SqliteStoreBase`，統一 6 個 Store |
| 4 | 拆 `trade_journal_backfill.py`（2051 行） | M | |
| 5 | coverage 門檻 60% → 70% | M | 須在主要重構穩定後再升，否則 CI 全紅 |

## Wave 3 — 長期

- 拆 `models.py`、`client.py`、`investor_fee_report_period.py`
- mypy 漸進導入（先 `cli/`、`engine/context.py`）
- Ruff `F841` ignore 清理、`pip-tools` 依賴鎖版
- Playwright 覆蓋擴充（WS、investor portal、error state）
- P5 規模化項目（PostgreSQL、Metabase、Sentry）依原計畫觸發條件啟動

---

## 衝突管理原則

- `engine/` 叢集（`base.py`、`management.py`、`execution.py`、`state_reconcile.py`）共用 context 與測試，**一次只動一檔**。
- coverage 門檻提升與任何大型重構不可同輪進行。
- 寬鬆 `except` 收斂只隨所在檔案的拆分一併處理，不做全域掃改。
