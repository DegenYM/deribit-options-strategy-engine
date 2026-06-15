# Scripts

## Production / ops

| Script | 用途 |
|--------|------|
| `run_live_profiles.py` | 多子帳 `run --live` 監督 |
| `check_live_heartbeat.py` | Live cycle 心跳 watchdog |
| `snapshot_investor_fee_nav.py` | 績效費 NAV 快照（cron） |
| `sweep_remaining_spot_profit.py` | 手動將 Remaining spot profit 賣成 USDT 並回寫 state（含 dust pool 合併小額 remainder）；等同 `./bot profit-sweep` |
| `backfill_ledger_equity_native.py` | 回填 frontend ledger 的 `equity_native_by_book`（含 Deribit 指數 API / 本地 `market.db`） |
| `align_premium_swap.py` | 修正 covered call premium 賣超／賣不足（state + 可選 buyback / sell） |
| `reconcile_premium_proceeds.py` | 依交易所 net sweep 重算各 group 的 `profit_sweep_quote_proceeds` |
| `repair_double_profit_sweep.py` | 修復重複 profit sweep 並買回多賣的原幣 |
| `run_e2e_dashboard.py` | Playwright E2E 啟動 mock dashboard |
| `live_launchd_all.sh` / `frontend_launchd_all.sh` | 批次 launchd 操作 |
| `generate_investor_onboarding_pdf.py` | 投資人 onboarding PDF |
| `generate_investor_fee_disclosure_pdf.py` | 績效費披露 PDF |
| `generate_investor_strategy_pdf.py` | 策略說明 PDF |
| `cleanup_legacy_layout.sh` | 本機 legacy 目錄清理（見 `docs/repo-layout-zh-TW.md`） |

Profit sweep 修復腳本需指定子帳 env（`--env-file` 或 `--investor` + `--account`）；預設 dry-run，寫入 state / 下單須加 `--live`。

## Dev / one-off（`scripts/dev/`）

| Script | 用途 |
|--------|------|
| `fix_dashboard_modules.mjs` | Dashboard 模組化重構輔助（legacy；模組化完成後通常不需再跑） |
| `check_dashboard_modules.mjs` | 靜態檢查 modules 未定義識別符 |
| `test_dashboard_boot.mjs` | 手動 boot operator dashboard |
| `test_dashboard_investor_boot.mjs` | 手動 boot investor portal |
| `test_overview_equity.mjs` | 手動驗證 overview equity 渲染 |
| `test_profit_disposition.mjs` | 手動驗證 profit disposition UI |
| `backfill_state_apr.py` | 回填 state 內已平倉 group 的 APR / PnL 索引 |
