# Canopy

獨立產品品牌 **Canopy**（樹冠）：在使用者已經持有的 Deribit 現貨上方，托管跑掩護性買權（Covered Call）。名稱來由見 [`docs/PRODUCT.md`](docs/PRODUCT.md)。

這不是代操、不是基金、不是投資建議。APR 與歷史績效**不是**收益承諾。詳見 [`legal/`](legal/)。

舊的 `deribit-options-strategy-engine` 投資人／AUM 流程**不**在 runtime 被引用。交易核心是抽出的副本，見 [`packages/cc_engine/EXTRACTED.md`](packages/cc_engine/EXTRACTED.md)。

## 目錄

- `apps/web` — dashboard（登入、金鑰、tier、暫停／緊急平倉）
- `apps/api` — FastAPI 控制面（auth、Stripe、desired-state、audit）
- `apps/supervisor` — 依 DB desired-state 啟動每租戶一個 worker
- `apps/marketd` — 全站一份 Deribit public 行情
- `packages/cc_engine` — Covered Call worker 函式庫
- `legal/` — 條款、隱私、風險揭露、行銷禁令
- `deploy/` — Docker Compose、備份、10 人基礎設施說明

## 本機啟動

`cc_engine` / `cc_saas` 不在系統 Python 路徑裡。請在 **`saas/`** 目錄安裝這個套件，不要在 repo 根目錄直接 `import cc_engine`。

```bash
git checkout cursor/covered-call-saas-1fce
cd saas
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp deploy/.env.example .env
```

確認 import：

```bash
python -c "from cc_engine.settings import CoveredCallSettings; print(CoveredCallSettings)"
```

啟動 API（editable install 之後不必再設 PYTHONPATH）：

```bash
uvicorn cc_saas.main:app --reload --port 8080
```

若不想 `pip install -e .`，改用：

```bash
cd saas
export PYTHONPATH=packages/cc_engine:apps/api:apps/supervisor:apps/marketd
python -c "from cc_engine.settings import CoveredCallSettings; print('ok')"
uvicorn cc_saas.main:app --app-dir apps/api --reload --port 8080
```

另開兩個行程：

```bash
python -m cc_supervisor.main
python -m cc_marketd.main
```

開發模式登入：`POST /api/auth/magic-link` 會回傳 `dev_token`。Waitlist 預設開啟，需 `POST /api/admin/approve`（管理員）或把第一個使用者在資料庫標成 `is_admin`。

開發訂閱：`ALLOW_DEV_BILLING=true` 時可用 `POST /api/billing/dev-subscribe`。

## 測試

```bash
cd saas
pytest -q
```

## 方案（僅掩護性買權）

| 方案 | USD／月 | 實單 | 幣 | tier | sweep |
|------|---------|------|----|------|-------|
| Scout | 49 | 否（僅模擬） | BTC | low | 否 |
| Trader | 99 | 需滿 7 天模擬 | 1 幣 | low／medium | 否 |
| Pro | 179 | 是 | BTC+ETH | 三檔 | 是 |
| Desk | 299 | 是 | BTC+ETH | 三檔 | 是（最多 3 子帳） |

Naked short／bull put／訊號 webhook 見 [`docs/PHASE2.md`](docs/PHASE2.md)。
