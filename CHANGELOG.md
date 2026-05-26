# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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
