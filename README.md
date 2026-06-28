# Deribit Options Strategy Engine

Automated Deribit option strategies for `BTC + ETH`, selectable via `OPTION_STRATEGY`.

GitHub: https://github.com/DegenYM/deribit-options-strategy-engine

This project is not affiliated with or endorsed by Deribit.

## Quick start

Configuration lives under `config/investors/<id>/` (sub-account credentials in `accounts/.env.<slug>`). **Do not** use a single repo-root `.env` anymore.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Create an investor directory (or ./bot investor init <id> --strategies naked,...)
cp -R config/investors/_example config/investors/youming
# Create .env.naked etc. from accounts/.env.<slug>.example and fill in API keys

export INVESTOR=youming   # subsequent commands can use --investor $INVESTOR

./bot --investor $INVESTOR --account naked ping
./bot --investor $INVESTOR --account naked scan --currencies BTC,ETH --json   # dry-run by default
./bot --investor $INVESTOR frontend
```

Full install, testing, and first run: [`docs/getting-started-zh-TW.md`](docs/getting-started-zh-TW.md)

## Strategy overview

| Strategy | Description |
|----------|-------------|
| `naked_short` | Single-leg short option (`put` / `call` / `both`) |
| `bull_put_spread` | Sell short put + buy long put protection leg |
| `covered_call` | Sell call covered by existing BTC/ETH spot |

- Optional `perp` delta hedge; `spot` is for abnormal inventory handling only
- **Dry-run by default**; add `--live` for real orders

Models, risk controls, and payoff diagrams: [`docs/strategies-zh-TW.md`](docs/strategies-zh-TW.md)

## Documentation

### Usage and configuration

| Doc | Contents |
|-----|----------|
| [Getting started](docs/getting-started-zh-TW.md) | Install, tests, minimal setup |
| [Strategies](docs/strategies-zh-TW.md) | Strategy models, risk controls, payoff sketches |
| [Configuration & env vars](docs/configuration-zh-TW.md) | Investor layout, env examples, performance-fee snapshots |
| [CLI commands](docs/cli-zh-TW.md) | Sub-account selection, common commands, `close-position` |
| [Local dashboard](docs/dashboard-zh-TW.md) | Dashboard, multi-investor, Tunnel / launchd |
| [Design notes](docs/design-notes-zh-TW.md) | State, report behavior |

### Investors and operations

| Doc | Contents |
|-----|----------|
| [Investor onboarding](docs/investor-onboarding-zh-TW.md) | Funding, sub-accounts, API keys |
| [Operator onboarding](docs/operator-onboarding-zh-TW.md) | `investor init`, registry |
| [Repo layout](docs/repo-layout-zh-TW.md) | Canonical layout, legacy migration |
| [Risk tiers](docs/investor-risk-tiers-apr-zh-TW.md) | Low / medium / high tier definitions |
| [Performance fee disclosure](docs/investor-fee-disclosure-zh-TW.md) | NAV, HWM, billing |
| [Telegram alerts](docs/telegram-alerts-zh-TW.md) | Alert setup |
| [Live incident runbooks](docs/runbooks/README-zh-TW.md) | State drift, 429, panic, Tunnel |
| [Optimization roadmap](docs/optimization-plan-zh-TW.md) | CI, ops, architecture split |

### Other

| Doc | Contents |
|-----|----------|
| [Backtest reports](docs/backtest/) | Offline research examples |
| [Frontend build / e2e](frontend/README.md) | Dashboard frontend |
| [Scripts](scripts/README.md) | Helper scripts |

## Investor layout

One directory per investor, with up to several strategy sub-accounts underneath. See the template at [`config/investors/_example/`](config/investors/_example/).

```bash
export INVESTOR=youming

./bot --investor $INVESTOR --account naked scan --currencies BTC,ETH --json
./bot --investor $INVESTOR --account naked manage --json
./bot --investor $INVESTOR frontend

# Live supervision (sub-accounts with live_enabled in accounts.toml)
python scripts/run_live_profiles.py --investor $INVESTOR --restart-failed
```

Most subcommands require `--account <slug>` (`frontend` is the exception—it aggregates all enabled sub-accounts for that investor). Load order and env examples: [`docs/configuration-zh-TW.md`](docs/configuration-zh-TW.md)
