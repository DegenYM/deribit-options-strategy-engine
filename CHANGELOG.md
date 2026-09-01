# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Portal snapshot cache: `portal_snapshots.db` per investor, shared `market.db`, background schedulers, and investor portal `source=portal_cache` bundle API.
- Covered call profit sweep (`COVERED_CALL_PROFIT_SWEEP_ENABLED`), `./bot profit-sweep`, and ops repair scripts (`align_premium_swap`, `reconcile_premium_proceeds`, `repair_double_profit_sweep`).
- Covered call auto spot restore (`COVERED_CALL_AUTO_SPOT_RESTORE_ENABLED`, default off): after ITM spot exit, live `manage` parks a GTC limit at the USDT breakeven cap (0.1% edge). Later cycles reconcile only and never market-buy.
- Covered call ITM → cash-secured put (`COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED`, default off): after ITM cover is sold (treated as USDC, including USDT journals after a manual convert), live `manage` sells a short-dated USDC put at/just below the assigned strike. Dashboard shows CSP journal rows, open-position groups, and From-ITM links.
- CSP premium target (`COVERED_CALL_CSP_PREMIUM_TARGET`, default `usdc`): with `spot`, live `manage` swaps each entered CSP's net premium (not the reserved assignment cash) into native coin via a `BTC_USDC` / `ETH_USDC` buy; capped to free USDC and retried while funds are short. Tracked on `csp_premium_swap_*` group fields.
- Local admin console (`./bot admin`, default `http://127.0.0.1:8750`): list every investor frontend, probe health, embed ops dashboard, start/stop/restart via launchd, and run Recover / close-position / panic-close (preview then `LIVE`). Loopback-only; trading actions stay off investor pages.
- Frontend ledger `equity_native_by_book` backfill (`scripts/backfill_ledger_equity_native.py`).
- Investor portal browser cache (`frontend/src/modules/investor-cache.js`) and design tokens (`frontend/tokens.css`).
- Dashboard frontend ES module sources (`frontend/src/`) with esbuild bundle to `app.js`.
- Playwright smoke tests and pytest HTTP smoke tests for dashboard pages and `/api/dashboard_bundle`.
- [`docs/cloudflare-access-checklist-zh-TW.md`](docs/cloudflare-access-checklist-zh-TW.md) for Zero Trust policy rollout.
- Linux systemd unit templates for live bot and dashboard frontend (`config/systemd/`).
- `./bot investor render-systemd` and generated units under `config/platform/generated/systemd/`.
- [`docs/live-profiles-systemd-zh-TW.md`](docs/live-profiles-systemd-zh-TW.md) runbook for Linux VPS deployment.
- Telegram alerts for live ops (`TELEGRAM_*` env vars, `./bot telegram-test`).
- GitHub Actions CI: pytest on Python 3.11/3.12, Ruff lint and format check.
- `pyproject.toml` with project metadata and tool configuration.
- `requirements-dev.txt` for development dependencies (pytest, ruff).

### Changed

- ITM → cash-secured put now treats a filled cover sale as USDC-eligible (including USDT journals after a manual convert) and skips auto-restore while the wheel is enabled. Dashboard treats CSP as its own strategy (`cash_secured`) next to covered call: same strategy card, open-group section, activity chip, credit/PnL rollup, and From-ITM link. `./bot scan --cash-secured` (or `--strategy csp`) dry-runs the same put picker as live manage and lists ranked USDC puts plus the IOC bid; already-entered parents preview with `would_place=false`. Default strike window is 5% below the ITM strike (`COVERED_CALL_CSP_STRIKE_FLOOR_PCT=0.05`). If fees/settlement leave too little USDC for a full-cover lot at the original strike, the scanner steps down strike within the floor window to refill cover. USDT parking-book drawdown (manual USDT→USDC convert) no longer trips hard derisk or blocks CSP. CSP entry takes the bid with IOC (no GTC mid park). Spread is not an entry gate. OI / notional stay on the modest CSP floors (6 / 3000). A prior mid-park `operator_cancelled` is retried. CSP holds to expiry; ITM expiry parks a USDC mid buy to restore cover.
- Canonical shared env file is `config/shared/.env.defaults` (`defaults.env` remains as a deprecated alias).
- Naked short put: `DEFENSE_CONFIRM_CYCLES=2`; medium `HARD_STOP_LOSS_PCT` 0.50 → 0.55. Shared TP / early-exit knobs moved to the strategy skeleton. Consecutive down days (`NAKED_ENTRY_DOWN_STREAK_DAYS=2`, 1.5% per day) elevate the regime so new shorts are not opened into a grind.
- Spot restore market buys place the native cover amount. The 0.1% USDT cushion is only a sufficiency / estimated-PnL check, so restore no longer overbuys ~0.5% and books negative USDT PnL.

### Fixed

- Covered-call auto restore now ceils the default buy quantity to the USDC linear min lot (BTC 0.01, ETH 0.1; capped at cover) instead of flooring to the spot step, and treats a cancelled/unfilled parked buy as `operator_cancelled` so live `manage` does not re-park it.
- Admin console empty-state CSS overrode the `hidden` attribute, so the placeholder stayed visible and squeezed the embedded investor dashboard into a header strip.
- Admin console now sizes the embedded ops dashboard to its content so the right pane scrolls as one page (investor toolbar + dashboard header move together).
- Dashboard static file path after `frontend_server/` package split (`parents[2]/frontend`).
- USDC book drawdown now uses `day_net_flow_usdc_by_book` (withdrawal/deposit adjustment).
- Fee snapshot tests use lowercase investor id (`demo`) to match manifest normalization.
- Covered-call drawdown shield test keeps exchange position in reconcile.

## [0.1.0] - 2026-05-20

Baseline for changelog tracking. Includes:

- Multi-strategy engine (`naked_short`, `bull_put_spread`, `covered_call`).
- Multi-investor config layout, platform registry, and investor ops CLI.
- Performance fee NAV snapshots, quarterly settlement, PDF/CSV reports.
- Dashboard with bundle API and parallel multi-account aggregation.
- macOS launchd templates for live bots and frontend.

[Unreleased]: https://github.com/DegenYM/deribit-options-strategy-engine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DegenYM/deribit-options-strategy-engine/releases/tag/v0.1.0
