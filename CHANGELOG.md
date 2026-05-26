# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- Dashboard static file path after `frontend_server/` package split (`parents[2]/frontend`).

### Added

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

### Fixed

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
