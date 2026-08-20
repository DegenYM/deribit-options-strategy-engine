# Extracted Covered Call engine

This tree is a **copy** of the trading core from `deribit-options-strategy-engine`,
minus investor registry, fee/HWM billing, frontend_server, Cloudflare/launchd ops, and CLI.

The SaaS worker talks to it only through `cc_engine.CoveredCallWorker`:

- `ping`
- `run_forever` / `run_cycles`
- `pause`
- `panic_close`

Product settings are `CoveredCallSettings` (tier, coins, sweep, credentials).
All other knobs live in `catalog/` and are not user-editable.

Do not import `deribit_engine` from the parent repository at runtime.
Bugfixes may be cherry-picked from the old repo while both products exist.
