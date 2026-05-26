# Dashboard frontend

Browser UI for the Deribit strategy dashboard. Source is ES modules under `src/`; production bundle is `app.js` (served by FastAPI).

## Build

Requires Node.js **18+**.

```bash
cd frontend
npm ci
npm run build    # writes app.js from src/
```

## Source layout

```text
src/
  main.js           # entry
  dashboard.js      # dashboard logic (split from legacy monolith)
  shared/
    config.js       # constants, formatters, strategies
    context.js      # investor mode, i18n, API base URL
    state.js        # mutable UI state
```

## E2E smoke tests (Playwright)

```bash
cd frontend
npm ci
npx playwright install chromium
npm run test:e2e
```

Starts a mocked dashboard via `../scripts/run_e2e_dashboard.py` unless `DASHBOARD_BASE_URL` is set.

HTTP-level smoke tests also run in pytest: `tests/e2e/test_dashboard_http_smoke.py`.
