# 設計備註

- 認證為了試用簡化，HTTP private request 直接走 Basic Auth
- 掃描同時支援 `quote_currency=settlement_currency=USDC` 的線性 options，以及 `quote_currency=settlement_currency=BTC/ETH` 的 reversed options
- `portfolio APR` 用 `annualized net pnl / REFERENCE_CAPITAL_USDC`
- 已平倉表的 **`Annualized`**：`(realized_pnl / 該筆倉位抵押名目) × (365 / holding days)`。covered call / 逆線 naked 分母通常為 `quantity`（1 BTC/ETH 每張）；USDC put 為 `strike × quantity`；bull put spread 為 `estimated_im_collateral`（max loss）。`realized_pnl` 仍為 USDC 等價；BTC／ETH 本位優先用 `realized_pnl_collateral_native`
- **`Return / max-loss`** 仍為 `realized_pnl / max_loss`（與上列年化分母口徑不同時，兩欄數字不必一致）
- 所有 `credit / debit / max loss / report` 內部都統一換算成 `USDC equivalent`
- 本地狀態保存在 `STATE_FILE`；多子帳建議使用 `.state/investors/<id>/<slug>.json`
- **State 寫入耐久性**：`StrategyStateStore.save` 與 heartbeat 皆走 `atomic_io.atomic_write_text`：寫 `.tmp` → `flush` + `fsync` → `os.replace` → best-effort fsync 目錄；斷電不會留下空檔或半截檔。封存 JSONL 用 `durable_append_text`（append + fsync）
- **State JSON 預設 compact**（`separators=(",", ":")`、無縮排、`sort_keys=True` 保持可 diff）。`STATE_JSON_PRETTY=true`（或 `StrategyStateStore(path, pretty=True)`）恢復縮排；`load` 兩種格式都吃
- **已平倉 group 封存（opt-in，預設關）**：`StrategyStateStore.archive_closed_groups(state, keep_recent_days=90, keep_min=20)` 把 `status == "closed"` 且 `closed_timestamp_ms` 早於 `keep_recent_days`、又不在最新 `keep_min` 筆內的 group，追加到 `<slug>.closed_archive.jsonl`（每行一個 `TradeGroup.to_dict()`，append-only、先寫封存再從 `state.groups` 移除、已在封存內的 id 不重寫 → 中途 crash 亦可自癒、重跑冪等），呼叫端再 `save`。讀完整歷史請用 `state.iter_all_groups(state, state_path)` / `load_archived_groups(path)`。設定：`STATE_CLOSED_ARCHIVE_ENABLED`（預設 `false`）、`STATE_CLOSED_ARCHIVE_KEEP_DAYS`（90）、`STATE_CLOSED_ARCHIVE_KEEP_MIN`（20）。維運用 `state.archive_closed_groups_for_state_file(path)`（bot 停機時執行）。**尚未接進 live cycle**：report / fee / dashboard 讀者仍直接讀 `state.groups`，接線前必須先改用 `iter_all_groups`
- **Public read cache 淘汰**（`public_cache.py`）：每 50 次寫入清一次 `stored_ms` 早於 `PUBLIC_CACHE_MAX_AGE_SECONDS`（預設 86400）的列，並把總列數壓到 `PUBLIC_CACHE_MAX_ROWS`（預設 5000，最舊先刪）
- **SQLite stores 共用基底**（`sqlite_store_base.SqliteStoreBase`）：market / portal / fee snapshot、transfer、trade journal、metrics 六個 store 共用連線（WAL、`synchronous=NORMAL`、`busy_timeout=30000`）、`_transaction()`、`_ensure_columns()` 遷移。`FeeSnapshotStore` / `TransferStore` / `TradeJournalStore` 新增 `purge_older_than(cutoff_ms=...)`，**不會自動呼叫**——財務紀錄保留期是營運決策
- **Live 監督**（`scripts/run_live_profiles.py`）：非阻塞重啟排程、每 profile 指數退避、啟動/重啟時依大小輪替 log、heartbeat 過期自動 SIGTERM/SIGKILL 後重啟；詳見 [`live-profiles-launchd-zh-TW.md`](live-profiles-launchd-zh-TW.md)
- `report` 讀本地 state 中已關閉 spread 的 realized 資料；若啟用 perp hedge，報表仍只統計 spread PnL，不含 perp hedge PnL
- `run` 會先做 `manage`，再在條件允許時嘗試 `enter-best`
