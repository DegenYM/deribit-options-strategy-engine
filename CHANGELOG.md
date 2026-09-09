# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Security

- Dashboard API token gate (`DASHBOARD_API_TOKEN`, off when empty): every `/api/*` request and `/ws/*` handshake must present `Authorization: Bearer` / `X-Dashboard-Token` (websocket also `?token=`). `DASHBOARD_API_TOKEN_EMBED=true` embeds the token as `<meta name="dashboard-api-token">` for setups already behind Cloudflare Access.
- **Breaking-ish:** dashboard CORS is opt-in via `DASHBOARD_CORS_ORIGINS` (comma list); the previous `*` default is gone and no CORS middleware is mounted when unset.
- Dashboard and admin console static routes serve an allowlist only (`index.html`, `investor*.html`, `admin.html`, bundles, CSS, `vendor/`); `package.json`, `src/`, `node_modules/`, `e2e/`, etc. return 404.
- Admin console rejects non-loopback **client addresses** in addition to the bind-host check, and gains an optional `ADMIN_CONSOLE_TOKEN` / `ADMIN_CONSOLE_TOKEN_EMBED` gate (`X-Admin-Token`). **Breaking-ish:** `--allow-public` now also skips the client-IP check.
- `/api/health`, `/api/status`, `/api/groups` no longer emit absolute filesystem paths (`state_file`, `account_env_file`, ledger / metrics paths are basenames).
- `BotConfig.__repr__` / `to_safe_dict()` mask `*secret*` / `*token*` / `*password*` fields; structured logs scrub the same keys. Dashboard/admin knobs (`dashboard_api_token`, `dashboard_cors_origins`, `admin_console_token`, …) are mirrored on `BotConfig` so they appear masked there too.
- Dependencies: pillow ≥ 12.3, ruff 0.15.x pinned everywhere, `pip-audit` (runtime + pdf sets) and Dependabot (pip / npm / actions) in CI, CI `concurrency` + `timeout-minutes`, Dockerfile `HEALTHCHECK`, `.dockerignore`.

### Added

- Opt-in closed-group archive: `STATE_CLOSED_ARCHIVE_ENABLED=true` makes the live `manage` cycle move closed groups older than `STATE_CLOSED_ARCHIVE_KEEP_DAYS` (default 90, always keeping the newest `STATE_CLOSED_ARCHIVE_KEEP_MIN`, default 20) into `<stem>.closed_archive.jsonl` before saving. `StrategyStateStore.archive_closed_groups` / `load_archived_groups` / `iter_all_groups` and `archive_closed_groups_for_state_file` for offline maintenance. Default off; readers still assume every group is in the state file.
- `TradeJournalStore.list_executions_by_groups()` bulk reader (chunked `IN (...)`, per-group cap) and `purge_older_than()` retention helpers on the fee-snapshot / transfer / trade-journal stores (never called automatically).
- `POST /api/trade_journal/sync` (was `GET`). **Breaking-ish:** the `GET` form now returns 405.
- `deribit_engine.atomic_io` (fsync'd atomic write / durable append), `deribit_engine.env_parse` (shared bool grammar), `deribit_engine.sqlite_store_base` (shared SQLite plumbing for the six per-investor stores).
- Strategy dynamism Wave 1–2 knobs: `ELEVATED_DELTA_MAX_TIGHTEN` (default 0.02), `ELEVATED_MAX_GROUPS_TIGHTEN` (default 0 / off), `NAKED_ALLOW_ELEVATED_ENTRY` (default **false**), `NAKED_ELEVATED_DELTA_MAX_TIGHTEN` (default 0.04), `NAKED_DYNAMIC_DELTA_ALLOW_CLOSER` / `NAKED_DYNAMIC_MIN_NET_APR_ALLOW_LOOSEN` (both default **false**), `ENABLE_DYNAMIC_MIN_NET_APR` plus `DYNAMIC_MIN_NET_APR_MAX_SHIFT` / `FLOOR` / `IVR_REF`. Shared covered_call **and** naked_short skeletons turn on dynamic target delta (strength **0.3**) and the MIN_NET_APR scaler; naked is OTM-only / tighten-only and still halts on elevated.
- CSP active roll (`COVERED_CALL_CSP_ACTIVE_ROLL_*`, **default off**): when an open cash-secured put is OTM, remaining DTE is still inside the roll window, time value is fat enough, both books clear liquidity, and a window candidate's **daily yield** beats holding remaining TV (`(new bid − switch fees) / new DTE > close-ask / remaining DTE`), live `manage` buys the put back (reduce_only) and sells the replacement IOC at the bid. Same or earlier expiry is allowed; same contract almost never wins the spread. Close-then-entry-fail retries entry only and will not re-sell a worse-daily put. Does not change OTM-expiry roll or ITM self-assign.
- `PUBLIC_CACHE_MAX_AGE_SECONDS` / `PUBLIC_CACHE_MAX_ROWS` eviction for the on-disk public read cache.
- `leg_risk` markers on spread entry / exit actions with Telegram alerts when one leg fills and the other does not.
- `frontend/npm run test:unit` (Node unit tests) runs in CI alongside the Playwright suite.
- CSP self-assign (`COVERED_CALL_CSP_SELF_ASSIGN_*`): when a wheel cash-secured put is confirmed ITM and either near expiry or time value is thin vs intrinsic, **and** the put book clears a liquidity gate (two-sided quotes, `MAX_SPREAD_RATIO` default 0.25, ask size ≥ close qty), live `manage` buys the put back and schedules USDC spot cover restore — so an intra-period dip is not lost to European expiry; illiquid short-DTE books wait for settlement instead.
- Portal snapshot cache: `portal_snapshots.db` per investor, shared `market.db`, background schedulers, and investor portal `source=portal_cache` bundle API.
- Covered call profit sweep (`COVERED_CALL_PROFIT_SWEEP_ENABLED`), `./bot profit-sweep`, and ops repair scripts (`align_premium_swap`, `reconcile_premium_proceeds`, `repair_double_profit_sweep`).
- Covered call auto spot restore (`COVERED_CALL_AUTO_SPOT_RESTORE_ENABLED`, default off): after ITM spot exit, live `manage` parks a GTC limit at the USDT breakeven cap (0.1% edge). Later cycles reconcile only and never market-buy.
- Covered call ITM → cash-secured put (`COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED`, default off): after ITM cover is sold (treated as USDC, including USDT journals after a manual convert), live `manage` sells a short-dated USDC put at/just below the assigned strike. Dashboard shows CSP journal rows, open-position groups, and From-ITM links.
- CSP premium target (`COVERED_CALL_CSP_PREMIUM_TARGET`, default `usdc`): with `spot`, live `manage` swaps realized CSP premium into native coin **after the put is closed/expired** (`max(0, entry_credit − close_debit − close_fee)`), waiting out any pending ITM cover restore first; capped to free USDC and remaining unspent premium on retries. Tracked on `csp_premium_swap_*` group fields.
- `spot-restore --instrument BTC_USDC` (or `ETH_USDC`) buys cover on that pair instead of the ITM exit quote, and live restore skips a cash-secured wheel so `manage` cannot re-sell a put.
- Admin console one-click **Close CSP + recover cover** (`POST /api/admin/investors/{id}/csp-abort-restore`): skip the cash-secured wheel, market-close the open put, then market-buy cover on the ITM exit pair (`BTC_USDC` / `ETH_USDC` when the wheel sold USDC). Ops-embed only; Preview then `LIVE`. Spot restore now follows the exit quote instead of always `*_USDT`.
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

- Covered-call `elevated` regime no longer full-halts new entries: scan/entry continue with a tighter effective `*_DELTA_MAX`. Naked short **still halts** on elevated (including the consecutive-down-day path) unless `NAKED_ALLOW_ELEVATED_ENTRY=true`, in which case the haircut is `NAKED_ELEVATED_DELTA_MAX_TIGHTEN` (default 0.04). `crisis` and `data_unavailable` still halt.
- **Breaking-ish:** `requirements.txt` / core `dependencies` no longer include the PDF stack (reportlab, matplotlib, numpy, pillow). Install with `pip install -e '.[pdf]'` or `-r requirements-pdf.txt` for fee-report PDFs and `scripts/generate_investor_*_pdf.py`. `pyproject.toml` gains `[build-system]`.
- Strategy state is written as compact JSON (`STATE_JSON_PRETTY=true` restores indentation); saves and archive appends fsync before rename / on append.
- `scripts/run_live_profiles.py` is a `LiveProfileSupervisor`: non-blocking restarts with per-profile exponential backoff, size-based log rotation at spawn boundaries, and heartbeat-stale detection that SIGTERM/SIGKILLs and reschedules a hung child.
- Dashboard performance: `/api/dashboard_bundle` ETag / 304, closed-groups payload reads trade-journal rows for all groups in one store open (was one store + query per group), ledger snapshot reads the newest file's tail instead of the whole file, background work runs on a bounded `ThreadPoolExecutor` with single-flight de-duplication for live refreshes, and charts update in place (`mountOrUpdateChart`) instead of re-mounting.
- Deribit client: `public/*` reads pace against the shared rate-limit key; `_cached_public_read` is single-flight per key; `get_instrument` is cached.
- `frontend/package.json` esbuild is `^0.25.0` again; `build.mjs` tags each bundle with a `//# dashboardBuildMode=<ops|investor>` footer so tests no longer depend on minifier constant folding.
- CSP OTM expiry now rolls: the same ITM parent sells another short-dated USDC put while cash remains and cover has not been restored. Parent dashboard shows cumulative wheel PnL (covered-call + closed CSP legs).
- CSP open-interest floor is per underlying: BTC `COVERED_CALL_CSP_MIN_OPEN_INTEREST` default **0.5**, ETH `COVERED_CALL_CSP_MIN_OPEN_INTEREST_ETH` default **5**. Bid + $3000 book notional still required. Native BTC/ETH book hard_derisk after an ITM cover sale no longer skips the USDC cash-secured put — only the USDC book can.

- ITM → cash-secured put now treats a filled cover sale as USDC-eligible (including USDT journals after a manual convert) and skips auto-restore while the wheel is enabled. Dashboard treats CSP as its own strategy (`cash_secured`) next to covered call: same strategy card, open-group section, activity chip, credit/PnL rollup, and From-ITM link. `./bot scan --cash-secured` (or `--strategy csp`) dry-runs the same put picker as live manage and lists ranked USDC puts plus the IOC bid; already-entered parents preview with `would_place=false`. Default strike window is 5% below the ITM strike (`COVERED_CALL_CSP_STRIKE_FLOOR_PCT=0.05`). If fees/settlement leave too little USDC for a full-cover lot at the original strike, the scanner steps down strike within the floor window to refill cover. USDT parking-book drawdown (manual USDT→USDC convert) no longer trips hard derisk or blocks CSP. CSP entry takes the bid with IOC (no GTC mid park). Spread is not an entry gate. OI / notional stay on the modest CSP floors (BTC 0.5 / ETH 5 / 3000). A prior mid-park `operator_cancelled` is retried. CSP holds to expiry; ITM expiry parks a USDC mid buy to restore cover.
- Canonical shared env file is `config/shared/.env.defaults` (`defaults.env` remains as a deprecated alias).
- Naked short put: `DEFENSE_CONFIRM_CYCLES=2`; medium `HARD_STOP_LOSS_PCT` 0.50 → 0.55. Shared TP / early-exit knobs moved to the strategy skeleton. Consecutive down days (`NAKED_ENTRY_DOWN_STREAK_DAYS=2`, 1.5% per day) elevate the regime so new shorts are not opened into a grind.
- Spot restore market buys place the native cover amount. The 0.1% USDT cushion is only a sufficiency / estimated-PnL check, so restore no longer overbuys ~0.5% and books negative USDT PnL.

### Fixed

- Unsafe (order-placing) requests are **not** re-sent after a `ConnectionError`, and `10028` / HTTP 429 on the unsafe path now feed the rate limiter instead of being retried blindly.
- Market-order fallback after a rejected `reduce_only` limit close only fires for Deribit price-band / post-only rejections (`is_market_fallback_eligible`: code `10007` / `price_too_*`); any other error no longer escalates to a market order.
- Profit sweep: a failed `get_user_trades_by_currency` raises `ProfitSweepTradesUnavailable` and the sweep is skipped for the cycle instead of being treated as "not swept yet" (no oversell).
- Silent `except: pass` sites in state reconcile / group exit / covered call now log the swallowed error.
- Dashboard scheduler `stop()` reports whether a thread was actually stopped; the live API identity used for cache keys / logs is a short sha256 digest instead of a string containing the raw `client_secret`.
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
