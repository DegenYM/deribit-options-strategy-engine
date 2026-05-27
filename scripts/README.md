# Scripts

## Production / ops

| Script | 用途 |
|--------|------|
| `run_live_profiles.py` | 多子帳 `run --live` 監督 |
| `check_live_heartbeat.py` | Live cycle 心跳 watchdog |
| `snapshot_investor_fee_nav.py` | 績效費 NAV 快照（cron） |
| `run_e2e_dashboard.py` | Playwright E2E 啟動 mock dashboard |
| `live_launchd_all.sh` / `frontend_launchd_all.sh` | 批次 launchd 操作 |
| `generate_investor_onboarding_pdf.py` | 投資人 onboarding PDF |
| `generate_investor_fee_disclosure_pdf.py` | 績效費披露 PDF |
| `generate_investor_strategy_pdf.py` | 策略說明 PDF |
| `cleanup_legacy_layout.sh` | 本機 legacy 目錄清理（見 `docs/repo-layout-zh-TW.md`） |

## Dev / one-off（`scripts/dev/`）

| Script | 用途 |
|--------|------|
| `fix_dashboard_modules.mjs` | Dashboard 模組化重構輔助（加 cross-module import） |
| `check_dashboard_modules.mjs` | 靜態檢查 modules 未定義識別符 |
| `test_dashboard_boot.mjs` | 手動 boot operator dashboard |
| `test_dashboard_investor_boot.mjs` | 手動 boot investor portal |
| `backfill_state_apr.py` | State 檔 closed-group APR 回填 |
