# 專案優化規格（2026-09 強化批次）

本文件記錄 2026-09-05 針對 Deribit Options Strategy Engine 的全面健檢結果、已實施的修正規格、新增設定項、行為變更與升級步驤，以及刻意延後項目的設計方案。
與 [`optimization-plan-zh-TW.md`](optimization-plan-zh-TW.md)（P0–P5 路線圖）互補：該文件是長期路線，本文件是本批次的實作規格與驗收紀錄。

適用對象：維護者 / 營運方。

---

## 0. 摘要

| 項目 | 健檢前 | 健檢後 |
|------|--------|--------|
| pytest | 873 passed / 21s | **1061 passed**（+188 測試） |
| coverage（`deribit_engine`） | 71.6% | ≥ 71.6%（新增程式碼皆附測試） |
| `ruff check` / `ruff format --check` | 1 error / 6 files 待格式化 | 全綠（ruff 0.15.16） |
| `pip-audit` | pillow 11.3.0 × 25 CVE | **0 vulnerabilities** |
| `npm audit` | 4（esbuild / postcss / nanoid / …） | **0 vulnerabilities** |
| Dashboard API 認證 | 無（僅靠 Cloudflare Access） | 可選 shared-token gate |
| CORS | `allow_origins=["*"]` 永遠開 | 預設不掛；白名單 opt-in |
| 非冪等下單遇 ConnectionError | 自動重送一次 | 不重送，交 reconcile |
| 平倉 limit 失敗 | 任何 `ExchangeError` 都降級市價 | 只有價格帶 / post-only 類才降級 |
| State 檔 | `indent=2` 全量重寫、無 fsync、closed 永不歸檔 | compact + fsync + opt-in 歸檔 |
| 監督腳本 | 阻塞式固定 delay 重啟、無 log rotation、不看 heartbeat | 非阻塞指數退避、啟動時輪替、heartbeat 過期自動重啟 |

變更規模：97 個 tracked 檔案（+6638 / −1558）、21 個新檔。**未 commit**，與使用者原本的 CSP wheel 未提交變更並存於工作樹。

---

## 1. 健檢方法

四個獨立審查面向平行進行，各自產出 file:line 證據，再由整合階段逐項核對原始碼：

1. **交易引擎與 client**（`client.py`、`engine/*`、`strategy.py`、`exchange_throttle.py`、`public_cache.py`）
2. **Dashboard 後端與前端**（`frontend_server/*`、`admin_server/*`、`frontend/src/*`）
3. **持久化與 ops**（`state.py`、`models.py`、`config.py`、六個 SQLite store、`scripts/run_live_profiles.py`）
4. **測試、CI、打包、repo 衛生、未提交變更**

另以工具量化：`ruff`、`pip-audit`、`npm audit`、AST 掃描長函式（36 個 >150 行）、`except Exception` 計數（全專案 189 處，引擎檔 69 處、其中 21 處靜默）。

---

## 2. 發現總表（依原優先級）

| # | 優先 | 面向 | 問題 | 狀態 |
|---|------|------|------|------|
| 1 | P0 | 工具鏈 | 工作樹 ruff 紅燈（I001 + 6 檔未格式化） | ✅ 已修 |
| 2 | P0 | 安全 | pillow `<12` 擋住 25 個 CVE 修補；重依賴混在 core | ✅ 已修 |
| 3 | P0 | 交易安全 | unsafe POST 重送、market fallback 過寬、sweep 抓取失敗吞錯、reconcile `pass` | ✅ 已修 |
| 4 | P0 | Dashboard 安全 | 無 auth、CORS `*`、health 洩路徑、GET 有副作用、static 整樹掛載、admin 只看 Host | ✅ 已修 |
| 5 | P1 | 持久化 | state 無界成長、pretty JSON、無 fsync | ✅ 已修（歸檔 opt-in） |
| 6 | P1 | Dashboard 效能 | groups N+1、ledger 全掃、bundle 雙 deepcopy 無 ETag、thread 無界 | ✅ 已修 |
| 7 | P1 | Rate limit | public 配額 per-IP 卻按 client_id 節流；10028 未回饋；cache stampede | ✅ 已修 |
| 8 | P1 | 多腿 | spread 進出非原子，殘腿無標記 | ⚠️ 最小版（標記 + 告警；combo 延後） |
| 9 | P1 | Ops | 監督腳本阻塞重啟、無退避、無 rotation、不看 heartbeat | ✅ 已修 |
| 10 | P1 | SQLite | 六份樣板、無 retention、public_cache 無 eviction | ✅ 已修（purge 不自動呼叫） |
| 11 | P2 | 結構 | 36 個 >150 行函式；12 mixin 無 Protocol；無 mypy | ⏸ 延後（§7） |
| 12 | P2 | 雙份邏輯 | Python `realized_summary` vs `domain.js` PnL；JS 測試不在 CI | ⚠️ JS 測試已入 CI；邏輯統一延後 |
| 13 | P2 | 前端資產 | 三份 HTML、兩份 committed bundle、104KB CSS、chart destroy/recreate | ⚠️ chart 已改 update；其餘延後 |
| 14 | P2 | 設定/模型 | `load_config` 438 行、兩套 bool parser、repr 洩 secret、`to_dict` 樣板 | ⚠️ repr / bool 已修；拆分延後 |
| 15 | P2 | 測試 | 1 個 parametrize、`test_engine` 4698 行、`simulation` 0% | ⏸ 延後 |
| 16 | P3 | CI | 無 concurrency/timeout/lockfile/pip-audit/Dependabot/型別檢查 | ✅ 已修（lockfile、mypy 延後） |
| 17 | P3 | 打包 | pyproject/requirements 重複、無 build-system、重依賴 | ✅ 已修 |
| 18 | P3 | Docker | `.dockerignore` 漏排、無 HEALTHCHECK | ✅ 已修 |
| 19 | P3 | 衛生 | `.preview_tmp`、docs 死鏈、`.env.example` 定位 | ✅ 已修 |

---

## 3. 已實施規格 — 交易引擎與 client

### 3.1 非冪等請求不重送
- **檔案**：`deribit_engine/client.py`
- **變更**：`UNSAFE_CONNECTION_RETRIES = 0`；`_unsafe_request` 遇 `ConnectionError` 立即 `raise TransientExchangeError("<method> connection failed; reconcile required")`，與 Timeout 分支語意一致。
- **理由**：連線可能在伺服器已接單、回應未讀完時中斷；重送會雙掛。
- **行為影響**：live cycle 收到 `TransientExchangeError` 會中止本 cycle 並 backoff；下一 cycle 由 `enable_adopt_exchange_positions` 認領未追蹤倉位。**若某 profile 關閉 adopt，需人工對帳**（見 §9）。
- **測試**：`tests/test_client.py`、`tests/test_client_unsafe_retry.py`。

### 3.2 節流模型
- **變更**：新增 `_pace_identity(method)`：`public/*` 一律以全域（主機層）key 節流與記錄 429/10028；`private/*` 維持 per-`client_id`。unsafe 路徑在 HTTP 429 / JSON-RPC `10028` 時也呼叫 `note_rate_limited`。
- **理由**：Deribit public 配額是 per-IP，同機多 investor 多 process 原本互不互斥。
- **測試**：`test_public_methods_pace_against_global_key`、`test_public_429_penalizes_global_key_not_client_id`、`test_unsafe_http_429_widens_private_identity_interval` 等。

### 3.3 Public read cache single-flight
- **變更**：`_cached_public_read` 對同 key 取 per-key lock，第一個 miss 載入、其餘等待後重讀；loader 例外時釋放並清除 inflight。`get_instrument` 也納入同一 TTL cache（`DERIBIT_INSTRUMENTS_CACHE_TTL_SEC`）。
- **測試**：`test_cached_public_read_single_flight_across_threads`、`test_get_instrument_uses_process_cache`。

### 3.4 平倉市價 fallback 允許清單
- **檔案**：`deribit_engine/engine/execution.py`
- **變更**：`is_market_fallback_eligible(exc)` 以 Deribit 錯誤碼（`10005/10007/10011/10023/10043/11054`）與訊息 token（`price_too_high/low`、`invalid_price`、`post_only_reject`、`price_wrong_tick`）判斷；非允許清單錯誤 → `_rejected_option_close_response()`（`rejected=True`、`filled_amount=0`）+ WARNING，`_close_leg_with_retry` 累計 `unfilled`，下一 cycle 重試。
- **理由**：資金不足、maintenance、reduce_only 衝突原本也會打市價。
- **測試**：`tests/test_execution_fallback.py`（10 項）。

### 3.5 Profit sweep 抓取失敗 ≠ 無成交
- **檔案**：`deribit_engine/profit_sweep_ops.py`
- **變更**：新增 `ProfitSweepTradesUnavailable`；`ProfitSweepTradeCache._ensure_currency` 失敗時 raise、不標 `_loaded`、記錄 `_unavailable`（同 cycle 不重打）。所有決策呼叫端改為保守：`refresh_*` → False、`guard_*_against_oversell` → 阻擋、`reschedule_*` 跳過 + WARNING、`_run_profit_sweep_pass` 對 unavailable 幣別發 `covered_call_profit_sweep_skipped`（reason `exchange_trades_unavailable`）、dust sweep 以 `_run_dust_pool_sweeps_guarded` 包住。
- **測試**：`tests/test_profit_sweep_unavailable.py`（10 項）。

### 3.6 靜默例外改為可觀測
- **檔案**：`engine/state_reconcile.py`、`engine/group_exit.py`、`engine/covered_call.py`、`profit_sweep_ops.py`
- **變更**：`except Exception: pass` 一律改 `LOGGER.warning(..., exc_info=True)`（影響金額/狀態）或 `LOGGER.debug`（診斷路徑）；reconcile 回退到過期 `current_debit` 時明確警告「realized PnL may be inaccurate」。控制流不變。
- **未做**：未在 `TradeGroup` 新增 `reconcile_note` 欄位持久化該標記（`close_reason` 被多處當 enum 判讀，塞自由文字風險高）；列入 §7。

### 3.7 CSP restore 帳戶摘要每 cycle 一次
- **檔案**：`engine/covered_call.py::_pending_cash_secured_cover_restore_actions`
- **變更**：`_account_summaries_by_currency()` 提到迴圈外；live 且抓取失敗 → 每組回 `cash_secured_cover_restore_skipped`（reason `usdc_balance_unavailable`），不下單；迴圈內以 `usdc_committed` 追蹤本 cycle 已花掉的 USDC。
- **測試**：`tests/test_csp_restore_summaries.py`。

### 3.8 價差殘腿標記與告警（最小版）
- **檔案**：`engine/entry.py`、`engine/execution.py`
- **變更**：第二腿失敗/部分成交時，action dict 帶 `leg_risk`（`orphan_long` / `excess_long_open` / `close_incomplete_long`）與 `leg_risk_quantity`，WARNING，並以 `event_key="leg_risk:<kind>:<group_id>"` 發 Telegram（incomplete 為 critical）。下單邏輯未動。
- **延後**：改用 `private/create_combo` 原子下單（§7.2）。
- **測試**：`tests/test_spread_leg_risk.py`（7 項）。

---

## 4. 已實施規格 — Dashboard 與 Admin

### 4.1 Shared-token 認證
- **檔案**：新 `frontend_server/auth.py`（`SharedTokenGate` ASGI middleware、`hmac.compare_digest`、`inject_token_meta`）、`app.py`、`routes/static.py`、`frontend/src/shared/context.js`（`apiFetch()` / `withWsToken()`）、`modules/domain.js`、`modules/dashboard-ws.js`。
- **規格**：`DASHBOARD_API_TOKEN` 未設 → 行為與以前完全相同。設定後 `/api/*`、`/ws/*` 需 `Authorization: Bearer <token>` 或 `X-Dashboard-Token`（WS 另接受 `?token=`）；缺少回 401 + `WWW-Authenticate: Bearer`。靜態 HTML/JS/CSS 維持公開。`DASHBOARD_API_TOKEN_EMBED=true` 才把 token 注入 HTML meta（僅適用 Cloudflare Access 後方的 operator dashboard）。
- **測試**：`tests/test_dashboard_hardening.py`（7 項）。

### 4.2 CORS 與副作用端點
- `GET /api/trade_journal/sync` → **POST-only**（GET 405）。
- CORS：預設**不掛** middleware；`DASHBOARD_CORS_ORIGINS`（逗號分隔）設定時只允許列出的 origin、`GET,POST`、`Authorization` / `X-Dashboard-Token` / `Content-Type`。

### 4.3 資訊洩漏
- `/api/health`、`/api/portfolio/snapshot`、`/api/status`、`/api/groups` 所有 `state_file` / `ledger_dir` / `metrics_db` / `account_env_file` 改為 basename，另加 `*_present` 布林。測試斷言 payload 不含 `/Users` / `/home`。
- `helpers._live_api_identity_config` cache key 改 `sha256(cid\0secret)[:16]`。

### 4.4 靜態檔白名單
- `routes/static.py` 重寫：只服務 4 個 HTML、`app.js` / `app-investor.js`、3 個 CSS、`favicon.svg`、`/vendor/*`；其餘 404。Admin 同樣白名單並恰好允許 `/src/admin.js`。Cache-Control 邏輯不變。

### 4.5 Admin console
- 同時要求 `request.client.host` 為 loopback（`127.0.0.1` / `::1` / `::ffff:127.0.0.1`）與 Host 檢查；`--allow-public` 才跳過兩者（**行為變更**：以前 `--allow-public` 仍被 Host 擋）。
- `ADMIN_CONSOLE_TOKEN` / `ADMIN_CONSOLE_TOKEN_EMBED` 語意同 4.1；`frontend/src/admin.js` 的 `adminFetch()` 401 時 prompt 一次並存 localStorage。交易動作鎖定 POST（GET 405）。

### 4.6 效能
- **groups N+1**：新增 `TradeJournalStore.list_executions_by_groups(scope_key, group_ids, *, per_group_limit=50)`（chunked `IN (...)` 500/批、`ORDER BY ts_ms DESC, id DESC`）；`groups_service._journal_executions_by_group` 改呼叫之。N 個 closed groups → 1 個 store 建構。
- **ledger 尾讀**：`helpers._latest_ledger_row` 以 64KB 反向 seek 讀最新檔最後一筆合法 JSON，往前一檔 fallback；`_append_ledger` 加 `flush()+fsync()`。
- **bundle ETag**：序列化一次 → `W/"sha1"`，`If-None-Match` 命中 304；移除路由內兩處多餘 deepcopy（`finalize_dashboard_bundle` 已 copy）。
- **有界背景池**：`make_background_executor()`（`ThreadPoolExecutor(max_workers=4, thread_name_prefix="dash-bg")`）+ `SingleFlightRunner`，注入 status / bundle / transfers 三個 SWR cache；lifespan 結束 `shutdown(wait=False, cancel_futures=True)`。
- **scheduler.stop()**：回傳 bool；join 超時記 WARNING、`running` 不翻、設 `stop_requested`。
- **Chart.js**：`mountOrUpdateChart()` 同 canvas 同 type 就地替換 data/options 後 `update('none')`；placeholder → 真資料仍重建。

### 4.7 前端 build
- `frontend/build.mjs` 為兩個 bundle 加 footer `//# dashboardBuildMode=ops|investor`；`tests/e2e/test_dashboard_http_smoke.py` 改斷言 footer 而非 minifier 常數字面。esbuild 解除釘選（`^0.25.0`，實裝 0.25.12）。

---

## 5. 已實施規格 — 持久化、設定、Ops

### 5.1 原子且耐久的寫入
- **新檔** `deribit_engine/atomic_io.py`：`atomic_write_text(path, text)`（tmp → flush → fsync → `os.replace` → best-effort 目錄 fsync；失敗清 tmp、原檔不動）、`durable_append_text`、`fsync_directory`。
- 使用點：`state.py::StrategyStateStore.save`、`live_heartbeat.py::write_live_heartbeat`。
- **未改**：`backtest_data.py:113`（非關鍵路徑）。

### 5.2 State 檔壓縮與歸檔
- **compact**：`serialize_state(state, pretty=False)` 預設 `indent=None, separators=(",", ":")`，`sort_keys=True` 保留。`StrategyStateStore(path, pretty=None)` → `BotConfig.state_json_pretty` / env `STATE_JSON_PRETTY`。load 兩種格式皆可。實測 30 groups 縮約 25%。
- **歸檔（opt-in，預設關）**：`StrategyStateStore.archive_closed_groups(state, *, keep_recent_days, keep_min, now_ms=None) -> int`：依 `closed_timestamp_ms` 挑選，先 durable append 到 `<stem>.closed_archive.jsonl`（每行一個 `TradeGroup.to_dict()`）再從 `state.groups` 移除；已在封存的 id 不重寫（crash 自癒、冪等）。輔助：`load_archived_groups(path)`、`iter_all_groups(state, path)`（live 優先去重）、`select_archivable_closed_groups`、`archive_closed_groups_for_state_file(path, ...)`。
- **排除條件**（本批次補上）：closed group 仍有非終態 `profit_sweep_status` / `spot_restore_status` / `csp_premium_swap_*`，或被仍在 `state.groups` 的子 group 以 `cash_secured_from_group_id` 引用（wheel parent），一律保留不歸檔。
- **接線**：`engine/management.py::manage()` 在 `live=True` 且 `state_closed_archive_enabled` 時、`_persist_trade_journal_actions` 之後、`state_store.save()` 之前呼叫；每 cycle 一次；`live=False` 不歸檔。
- **啟用前置條件**（§8）。

### 5.3 SQLite 基底與 retention
- **新檔** `deribit_engine/sqlite_store_base.py`：`SqliteStoreBase`（`_lock`、`_connect()` timeout 30 / WAL / `synchronous=NORMAL` / `busy_timeout=30000`、`_transaction()`、`_init_db()` + `_migrate(conn)` hook、`_ensure_columns`、`_delete_where`、`_table_row_count`）。`foreign_keys` 未開（無 schema 使用 FK）。
- 六個 store 改為繼承，公開 API 不變。
- 新增 `purge_older_than(cutoff_ms=...)`：`FeeSnapshotStore`（只刪 `nav_snapshots`；settlements / hwm / flow_baseline 永不刪）、`TransferStore`（`transfer_rows`；`transfer_sync_meta` 保留）、`TradeJournalStore`（`trade_executions` + 已平倉 `trade_group_stats`）。**皆不自動呼叫**——財務紀錄 retention 是營運決策；建議策略見 §8.3。
- `public_cache.py`：每 50 次寫入 `evict()`：刪早於 `PUBLIC_CACHE_MAX_AGE_SECONDS`（預設 86400、下限 60）者，再壓到 `PUBLIC_CACHE_MAX_ROWS`（預設 5000、下限 100）。新增 `idx_public_reads_stored_ms`。

### 5.4 設定
- `BotConfig.__repr__` / `__str__` 改走 `to_safe_dict()`；欄位名含 `secret/token/password/passwd/api_key/private_key` 遮成 `***` + 末 2 碼（`*_embed` 布林排除）。
- **新檔** `deribit_engine/env_parse.py`：`parse_env_bool(value, *, default, strict, name)`；`config._to_bool` strict（→ `ConfigurationError`），`telegram_alerts._truthy` 非 strict + WARNING。
- `config.py` 模組 docstring 加欄位分組地圖（identity / state / scan / entry / scoring / liquidity / risk / exits / hedge / covered_call / sweep / timing），作為未來拆分依據。
- `structured_log.py` scrub 清單加 `client_secret` / `telegram_bot_token` / `dashboard_api_token` / `admin_console_token`；value scrubber 新增 `*_token=`、`Authorization: Bearer`、`X-API-Token`，case-insensitive。

### 5.5 `scripts/run_live_profiles.py` 監督腳本
- 重構為 `LiveProfileSupervisor` / `ProfileRuntime` / `SupervisorSettings`，clock / spawn / terminate / notify / read_heartbeat 可注入。
- **非阻塞重啟**：每 profile `restart_at`，主循環每秒輪詢。
- **指數退避**：`delay = min(base × 2^(n−1), max)`；`--restart-max-delay-seconds`（600）、`--restart-stable-seconds`（900，穩定後歸零）。Telegram `bot_exit` / `bot_restart` 含 `attempt=` / `next_restart_in=`。
- **Log 輪替**：只在啟動/重啟時依大小輪替 `<slug>.log.1..N`（`--log-max-bytes` 50MB、`--log-backups` 5）。**限制**：子程序 fd 直寫，長期不重啟的 log 會超過上限直到下次重啟（設計取捨：避免監督腳本掛掉造成子程序 EPIPE）。
- **Heartbeat 自動重啟**：子程序 uptime ≥ 門檻且 heartbeat `ts_ms` 過期 → SIGTERM → `--heartbeat-kill-grace-seconds`（30）→ SIGKILL → 退避重啟；`event_key=live_heartbeat_stale_restart:<env>`；`--heartbeat-stale-seconds` 預設 `LIVE_HEARTBEAT_STALE_SECONDS` / 600；`--no-heartbeat-restart` 關閉；只在 `--restart-failed` 下生效；heartbeat 檔不存在只警告一次不殺。
- 既有 launchd plist 無需修改（新 flag 皆有預設）。
- **測試**：`tests/test_run_live_profiles.py`（17 項，fake clock / process）。

---

## 6. 已實施規格 — 工具鏈、CI、打包

### 6.1 依賴
- `pyproject.toml` 為 source of truth（加 `[build-system]` setuptools）；`requirements*.txt` 鏡像。
- core：`requests`、`python-dotenv`、`fastapi`、`uvicorn[standard]`。
- `[pdf]` extra / `requirements-pdf.txt`：`reportlab>=4,<5`、`matplotlib>=3.8,<4`、`numpy>=1.26,<3`、`pillow>=12.3,<13`。`investor_fee_report_pdf.py` 與 `scripts/generate_investor_*_pdf.py` 改 lazy import，缺少時提示 `pip install -e '.[pdf]'`。
- `[dev]`：`pytest`、`pytest-cov>=6,<8`、`pytest-socket`、`ruff>=0.15.16,<0.16`、`pre-commit`、`httpx`、`pip-audit`。
- npm：esbuild `^0.25.0`。

### 6.2 CI（`.github/workflows/ci.yml`）
- 頂層 `concurrency`（同 ref 取消舊 run）。
- `test`：`timeout-minutes: 20`；新增 blocking `pip-audit -r requirements.txt -r requirements-pdf.txt`。
- `frontend`：`timeout-minutes: 30`；Python 只裝 `requirements.txt` + pytest；Playwright 前新增 `npm run test:unit`（`scripts/dev/run_unit_tests.mjs` 跑 `test_profit_disposition` / `test_open_group_dedupe` / `test_overview_equity`）。
- `.github/dependabot.yml`：pip / npm(`/frontend`) / github-actions，weekly，minor+patch 分組。
- `.pre-commit-config.yaml` ruff rev 與 pyproject 對齊（v0.15.16）。

### 6.3 Docker
- `.dockerignore` 排除 tests / docs / output / `.pip_packages` / `.preview_tmp` / `.github` / `**/*.md` / `scripts/generate_*.py` / `scripts/dev` / 秘密檔；frontend 只保留可服務的靜態檔（含 `src/admin.js`）。
- `Dockerfile`：`WITH_PDF` build arg；內嵌 `/usr/local/bin/healthcheck.py` + `HEALTHCHECK`，依 PID 1 cmdline 判斷角色：`live` → `scripts/check_live_heartbeat.py --dry-run`（exit 1 = stale）；`frontend` → 打 `/api/health`。

### 6.4 衛生
- `.gitignore` 加 `.preview_tmp/`、`*.egg-info/`、`build/`。
- `docs/operator-onboarding-zh-TW.md` 死鏈改指 `fee-payout-addresses.toml.example`。
- `.env.example` 頂部標示 legacy；`docs/configuration-zh-TW.md` 註明僅供參考、引擎不載入。
- `config/investors/_example/.env.investor.example` 追加本批次所有新 env（含註解與預設值）。
- `CHANGELOG.md` `[Unreleased]` 新增 Security 區段並補齊 Added / Changed / Fixed。

---

## 7. 新增設定總表

### 7.1 環境變數

| 變數 | 預設 | 用途 |
|------|------|------|
| `DASHBOARD_API_TOKEN` | 空（停用） | `/api/*` `/ws/*` shared token |
| `DASHBOARD_API_TOKEN_EMBED` | `false` | 是否把 token 注入 HTML meta |
| `DASHBOARD_CORS_ORIGINS` | 空（不掛 CORS） | 逗號分隔 origin 白名單 |
| `ADMIN_CONSOLE_TOKEN` | 空 | admin console token |
| `ADMIN_CONSOLE_TOKEN_EMBED` | `false` | 同上 embed |
| `STATE_JSON_PRETTY` | `false` | state 檔 `indent=2` |
| `STATE_CLOSED_ARCHIVE_ENABLED` | `false` | live cycle 歸檔 closed groups |
| `STATE_CLOSED_ARCHIVE_KEEP_DAYS` | `90` | closed 保留天數 |
| `STATE_CLOSED_ARCHIVE_KEEP_MIN` | `20` | 最少保留最新 N 個 closed |
| `PUBLIC_CACHE_MAX_AGE_SECONDS` | `86400`（下限 60） | public cache 最大存活 |
| `PUBLIC_CACHE_MAX_ROWS` | `5000`（下限 100） | public cache 行數上限 |

以上皆已加入 `BotConfig`（`dashboard_*` / `admin_console_*` 欄位以 `to_safe_dict()` 遮罩）。

### 7.2 `run_live_profiles.py` 新 CLI 旗標

| 旗標 | 預設 |
|------|------|
| `--restart-max-delay-seconds` | 600 |
| `--restart-stable-seconds` | 900 |
| `--log-max-bytes` | 52428800 |
| `--log-backups` | 5 |
| `--heartbeat-stale-seconds` | `LIVE_HEARTBEAT_STALE_SECONDS` / 600 |
| `--heartbeat-kill-grace-seconds` | 30 |
| `--no-heartbeat-restart` | off |

### 7.3 新 action / reason 鍵（前端可選擇顯示）
- action dict：`leg_risk`、`leg_risk_quantity`
- reason：`usdc_balance_unavailable`、`exchange_trades_unavailable`
- close response：`rejected`

---

## 8. 行為變更與升級步驟

### 8.1 破壞性 / 需注意
1. `GET /api/trade_journal/sync` → **POST**。若有 cron / 外部呼叫請改 method。
2. CORS 預設不再 `*`。跨 origin 存取需設 `DASHBOARD_CORS_ORIGINS`。
3. `requirements.txt` 不含 PDF 依賴。產 PDF 的環境改 `pip install -e '.[pdf]'` 或 `-r requirements-pdf.txt`。
4. admin `--allow-public` 現在同時跳過 Host 與 client-IP 檢查（原本仍受 Host 擋）。
5. `/api/*` payload 中所有路徑欄位改為 basename。
6. 非冪等下單遇 ConnectionError 不再重送：**確認每個 live profile 皆啟用 adopt exchange positions**，否則需人工對帳流程。
7. 平倉 limit 遇非價格帶錯誤不再打市價，改下一 cycle 重試；hard-stop 情境請確認告警可及時觸達。
8. State 檔改 compact JSON（人眼閱讀請 `python -m json.tool` 或設 `STATE_JSON_PRETTY=true`）。

### 8.2 升級步驟
```bash
source .venv311/bin/activate
pip install -e '.[dev,pdf]'          # 或 pip install -r requirements-dev.txt
pre-commit install                    # ruff rev 已更新
cd frontend && npm ci && npm run build && npm run test:unit && cd ..
pytest tests/ -q                      # 期望 1061 passed
ruff check . && ruff format --check .
pip-audit -r requirements.txt -r requirements-pdf.txt
```
Dashboard 若經 tunnel 對外：設定 `DASHBOARD_API_TOKEN`，確認 Cloudflare Access 仍在前方，並視需要設 `DASHBOARD_CORS_ORIGINS`。

### 8.3 啟用 closed-group 歸檔前置條件
`STATE_CLOSED_ARCHIVE_ENABLED=true` **前**，以下讀者必須改用 `state.iter_all_groups(state, state_path)` 或 `load_archived_groups`，否則 lifetime 統計會少算：
`investor_fee_report_period.py`（`_iter_period_closed_groups`）、`realized_summary.py`、`frontend_server/groups_service.py`、`frontend_server/app.py`、`trade_journal_backfill.py`、`fee_discount.py`、`profit_sweep_ops.py` / `profit_sweep_dust.py` / `profit_sweep_repair.py`、`spot_restore_ops.py`、`admin_server/actions.py`、`investor_nav_snapshot.py`、`engine/base.py::report()`；`metrics_store.sync_from_closed` 的 fingerprint 也需含封存檔。`archive_closed_groups_for_state_file` 只可在該 bot **停機**時對既有檔執行。

SQLite purge 建議政策（不自動執行，由營運排程呼叫）：
- `FeeSnapshotStore.purge_older_than(cutoff_ms=<上一結算期起點 − 1 年>)`
- `TransferStore.purge_older_than`：絕不早於 flow baseline `start_timestamp_ms`
- `TradeJournalStore.purge_older_than`：保留 ≥ 400 天（最長報表回溯期）

---

## 9. 已知殘餘風險與監控

| 風險 | 說明 | 監控 / 緩解 |
|------|------|-------------|
| 未追蹤倉位 | §3.1 不重送後，接單但回應丟失的訂單靠下一 cycle adopt | 確認 adopt 啟用；Telegram `TransientExchangeError` 告警 |
| 殘腿 | spread 仍序列下單 | `leg_risk` Telegram critical；§10.2 combo 為根治 |
| Log 超上限 | 只在重啟時輪替 | 外部 logrotate（copytruncate）或定期重啟 |
| 歸檔少算 | 讀者未切換前勿啟用 | flag 預設關；§8.3 |
| `TradeJournalStore` 建構會 `CREATE TABLE IF NOT EXISTS` | dashboard 每次 closed-groups 載入多一次 schema init 連線 | 成本極低；可在 base 加 `readonly` 選項 |
| starlette 1.6 `TestClient` httpx 已標 deprecated | 目前只是 warning | 升級 dev extra 時處理 |

---

## 10. 刻意延後項目（P2）與設計方案

### 10.1 巨型函式與 mixin 契約
- **現況**：36 個 >150 行函式（`backtest.run_backtest` 759、`runtime_setup.build_runtime_setup` 676、`config.load_config` 438、`spot_restore_ops.execute_spot_restore_for_group` 393、`cli/strategy.register_parsers` 327…）；`DeribitOptionTrialBot` 由 12 個 mixin 組成、無 `Protocol`。
- **方案**：(1) 為每個 mixin 定義 `typing.Protocol`（`_EngineHost`）宣告其依賴的 `self.*` 成員，在 `TYPE_CHECKING` 下標註；(2) 引入 `mypy --strict-optional` 於 CI 以 `continue-on-error` 起步，逐模組加 `# mypy: strict`；(3) 拆分順序：`execute_spot_restore_for_group` → plan / place / await / apply / persist；`build_runtime_setup` → 依 scheduler 分段；`load_config` → 依 §5.4 分組地圖拆 `RiskConfig` / `CoveredCallConfig` / `CspConfig` … 再 compose。每 PR 只拆一塊。

### 10.2 多腿原子下單
- **方案**：bull put 進場與 spread 平倉改用 `private/create_combo` + 單筆 combo order；不可用時 fallback 到現行序列 + `leg_risk`。需在 `TradeGroup` 新增 `combo_instrument_name`、`leg_risk`（持久化）與 `reconcile_note` 欄位，reconcile 對 `leg_risk` 非空的 group 不得標 closed。

### 10.3 前後端 PnL 單一真相
- **現況**：`realized_summary.py` 與 `domain.js`（5,682 行）各自實作 profit disposition / CSP split / ITM fold。
- **方案**：後端在 groups payload 直接下發 `profit_disposition`、`wheel_pnl` 等已算欄位；前端只格式化。以 `scripts/dev/test_*.mjs`（已入 CI）為回歸基線，先做 golden-file 對照確認兩邊結果一致，再刪 JS 側計算。之後 `domain.js` 拆 `fmt` / `pnl` / `html`。

### 10.4 前端資產
- 三份 HTML → 單一模板 + locale JSON；`i18n("en","中文")` 收斂為訊息目錄。
- committed bundle：改為 CI 驗證 `npm run build` 後 `git diff --exit-code frontend/app*.js`，或 gitignore 產物並在部署時 build。
- `styles.css` 依 ops / investor 入口拆 entry，或 PurgeCSS。
- 全量 `innerHTML` 重繪 → 只重畫變更區塊；WS fresh 時跳過 REST。
- 加 CSP header；字體 self-host。

### 10.5 測試
- 引入 `parametrize` 取代複製貼上（目前 872 測試僅 1 處）；`test_engine.py` 依 mixin 拆檔；`conftest.FakeClient` 移到 `tests/fakes/`。
- 覆蓋空洞：`simulation.py` 0%、`report_md.py` 9%、`backtest.py` 15%、`portal_snapshot_scheduler.py` 29%、`routes/core.py` 34%、`engine/scanner.py` 50%。若 `simulation` / `backtest` 非產品路徑，改在 coverage `omit` 明示。
- coverage 門檻 60% → 70%（目前實際 ~72%，可直接調）。

### 10.6 其他
- Dependency lockfile（`uv lock` / `pip-compile`）。
- `models.TradeGroup.to_dict/from_dict` 以欄位 metadata 產生，compat shim 集中到 migration 層。
- `frontend_server/auth.py` 改讀 `BotConfig` 而非 `os.environ`（目前兩者同源，行為一致）。
- `run_live_profiles` log 輪替改 reader-thread 或 copytruncate。

---

## 11. 驗收紀錄

```
pytest tests/ -q                     1061 passed, 14 warnings
ruff check .                         All checks passed
ruff format --check .                233 files already formatted
npm run build                        OK (app.js 188.9kb / app-investor.js 192.0kb)
npm run test:unit                    3/3
npm audit                            found 0 vulnerabilities
pip-audit -r requirements.txt -r requirements-pdf.txt   No known vulnerabilities
pytest tests/e2e -q                  10 passed
git diff --stat                      97 files changed, +6638 / −1558 (另 21 個新檔)
```

Playwright e2e 未在本機執行（本機 Node 16 與 playwright-core 不相容；CI 為 Node 22）。

---

## 12. 相關文件
- [`optimization-plan-zh-TW.md`](optimization-plan-zh-TW.md) — 長期路線圖
- [`roadmap-2026H2-zh-TW.md`](roadmap-2026H2-zh-TW.md) — Wave 1–3
- [`live-profiles-launchd-zh-TW.md`](live-profiles-launchd-zh-TW.md) — 監督腳本參數
- [`runbooks/README-zh-TW.md`](runbooks/README-zh-TW.md) — heartbeat 自動重啟處置
- [`dashboard-zh-TW.md`](dashboard-zh-TW.md) / [`cloudflare-access-checklist-zh-TW.md`](cloudflare-access-checklist-zh-TW.md) — token 與 Access 配置
- [`../CHANGELOG.md`](../CHANGELOG.md)

---

## 13. 策略動態化 Wave 1–2（本波）

本節只記錄 **策略動態化** 的 knobs 與小 scaler，**不重寫**上文健檢規格。約束：不啟用 `bull_put`、不默默改 `config/investors/{an,eugene,jack,ma,pat,youming}/` 的資金 knobs。CSP 主動 roll 已在 Wave 3 實作，**預設關**。

### 13.1 Wave 1 — 接上既有能力

- **動態 target delta**：程式原本就有 `ENABLE_DYNAMIC_TARGET_DELTA` + VRP 位移，只動**偏好帶內的排序目標**，不改硬 `*_DELTA_MAX/MIN`。本波在 shared `covered_call` 與 `naked_short` 骨架打開，strength **0.3**（不是引擎預設 0.5）。**Naked 不對稱**：富 VRP 仍往更 OTM；薄 VRP **不**往 ATM 拉（`NAKED_DYNAMIC_DELTA_ALLOW_CLOSER=false`）。Covered call 維持雙向。
- **營運 playbook（只寫文件 / `_example` 註解）**：youming 可把 covered_call `risk_tier` 對齊 low；jack 可在觀察 manage dry-run 後開 `COVERED_CALL_CSP_SELF_ASSIGN_ENABLED`；an / ma / pat 維持 settlement。

### 13.2 Wave 2 — 小 scaler

- **elevated 收緊、不停 covered_call**：`crisis` 仍全停；`elevated` 對 covered_call 允許進場，有效 call/put `delta_max` 減 `ELEVATED_DELTA_MAX_TIGHTEN`（預設 0.02，不低於 `*_DELTA_MIN`）。`ELEVATED_MAX_GROUPS_TIGHTEN` 預設 **0**（關），僅在設成正整數時把 `MAX_GROUPS_PER_CURRENCY` 減 N 且仍 ≥ 1。
- **IVR / VRP → MIN_NET_APR**：`ENABLE_DYNAMIC_MIN_NET_APR` 在 covered_call **與 naked_short** 骨架為 true。Covered call 最多 ±0.005（低 IVR 可放寬，下限 `DYNAMIC_MIN_NET_APR_FLOOR` 或 `MIN_NET_APR × 0.7`）。**Naked 預設只收緊、不放寬**（`NAKED_DYNAMIC_MIN_NET_APR_ALLOW_LOOSEN=false`）：不因低波動去賣便宜的下跌保險。
- **naked elevated 與 CC 不同**：`NAKED_ALLOW_ELEVATED_ENTRY` 預設 **false**（含 down-streak → 停開）。若顯式 `true`，用 `NAKED_ELEVATED_DELTA_MAX_TIGHTEN`（預設 **0.04**，大於 CC 的 0.02）。`crisis` 仍全停。
- **CSP DTE**：只補文件與 `_example`；live eugene/youming/jack 值不動；預設仍 2–10。

### 13.3 CSP 主動 roll（Wave 3 已實作，預設關）

主動 roll（未到期就買回仍 OTM 的 CSP、再賣一張窗內**日收益更高**的 put；同一張或更早到期也可以）已接上 `manage`，master switch `COVERED_CALL_CSP_ACTIVE_ROLL_ENABLED` **預設 false**。閘門：流動性（平倉重用 self-assign 價差／ask 量；替換約走既有 CSP OI／名目＋同一價差上限）、`(新 bid − 換倉費) / 新 DTE > 平倉 ask / 剩餘 DTE`。履約價仍用母倉 `COVERED_CALL_CSP_STRIKE_FLOOR_PCT`，不是 covered-call 動態 delta。Live 先平倉再 IOC bid 開新約；新約失敗只重試進場，且不會再賣日收益更差的同一張。未改 live `config/investors/{an,eugene,jack,ma,pat,youming}/`。`bull_put` 未啟用。

測試：`tests/test_dynamic_entry_scalers.py`、`tests/test_csp_active_roll.py`。
