# Covered Call SaaS

獨立產品：**使用者自備 Deribit API Key（BYOK）**，雲端托管只跑 **Covered Call**。

這不是代操、不是基金、不是投資建議。APR 與歷史績效**不是**收益承諾。詳見 [`legal/`](legal/)。

舊的 `deribit-options-strategy-engine` 投資人／AUM 流程**不**在 runtime 被引用。交易核心是抽出的副本，見 [`packages/cc_engine/EXTRACTED.md`](packages/cc_engine/EXTRACTED.md)。

## 目錄

- `apps/web` — dashboard（登入、金鑰、tier、Pause／Panic）
- `apps/api` — FastAPI 控制面（auth、Stripe、desired-state、audit）
- `apps/supervisor` — 依 DB desired-state 啟動每租戶一個 worker
- `apps/marketd` — 全站一份 Deribit public 行情
- `packages/cc_engine` — Covered Call worker 函式庫
- `legal/` — 條款、隱私、風險揭露、行銷禁令
- `deploy/` — Docker Compose、備份、10 人基礎設施說明

## 本機啟動

```bash
cd saas
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp deploy/.env.example .env
# 可選：docker compose -f deploy/compose.yml up --build
uvicorn cc_saas.main:app --app-dir apps/api --reload --port 8080
```

另開兩個行程：

```bash
PYTHONPATH=packages/cc_engine:apps/api:apps/supervisor python -m cc_supervisor.main
PYTHONPATH=packages/cc_engine:apps/api:apps/marketd python -m cc_marketd.main
```

開發模式登入：`POST /api/auth/magic-link` 會回傳 `dev_token`。Waitlist 預設開啟，需 `POST /api/admin/approve`（管理員）或把第一個使用者在資料庫標成 `is_admin`。

開發訂閱：`ALLOW_DEV_BILLING=true` 時可用 `POST /api/billing/dev-subscribe`。

## 測試

```bash
cd saas
PYTHONPATH=packages/cc_engine:apps/api:apps/supervisor:apps/marketd pytest -q
```

## 方案（僅 Covered Call）

| 方案 | USD／月 | 實單 | 幣 | tier | sweep |
|------|---------|------|----|------|-------|
| Scout | 49 | 否（dry-run） | BTC | low | 否 |
| Trader | 99 | 需滿 7 天 dry-run | 1 幣 | low／medium | 否 |
| Pro | 179 | 是 | BTC+ETH | 三檔 | 是 |
| Desk | 299 | 是 | BTC+ETH | 三檔 | 是（最多 3 子帳） |

Naked short／bull put／訊號 webhook 見 [`docs/PHASE2.md`](docs/PHASE2.md)。
