# Dashboard frontend

Browser UI for the Deribit strategy dashboard. Source is ES modules under `src/`; production bundles are `app.js` + `tailwind.css` (served by FastAPI).

## Build

Requires Node.js **18+**.

```bash
cd frontend
npm ci
npm run build    # tailwind.css + minified app.js (verify in browser before commit)
```

**注意**：`app.js` 與 `tailwind.css` 為 runtime 交付檔；`npm run build` 會覆寫它們。若 index 頁樣式或互動異常，請重新 build 或確認產物存在。

## Source layout

```text
src/
  main.js              # entry
  dashboard.js         # init, controls, render orchestration
  modules/
    domain.js          # formatters, trade groups, fetch, PnL helpers
    render.js          # DOM render (topbar, cards, activity, stress)
    charts.js          # Chart.js panels
    refresh.js         # data refresh + investor load UX
  shared/
    config.js          # constants, formatters, strategies
    context.js         # investor mode, i18n, API base URL
    state.js           # mutable UI state
  tailwind-input.css   # @tailwind source (→ ../tailwind.css)
tailwind.config.js
styles.css             # custom component CSS (non-Tailwind)
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
