# Dashboard frontend

Browser UI for the Deribit strategy dashboard. Source is ES modules under `src/`; production bundles are `app.js` + `tailwind.css` (served by FastAPI).

## Build

Requires Node.js **18+**.

```bash
cd frontend
npm ci
npm run build    # tailwind.css + minified app.js / app-investor.js (verify in browser before commit)
```

**注意**：`app.js`、`app-investor.js` 與 `tailwind.css` 為 runtime 交付檔；`npm run build` 會覆寫它們。若 index 頁樣式或互動異常，請重新 build 或確認產物存在。

**Design tokens**：`tokens.css` 為獨立靜態檔（CSS variables），不由 build 產生；`index.html` / `investor.html` 在 `tailwind.css` 之後、`styles.css` 之前載入。

## Source layout

```text
src/
  main.js              # entry (operator + investor bundle via __BUILD_INVESTOR__)
  dashboard.js         # init, controls, render orchestration
  modules/
    domain.js          # formatters, trade groups, fetch, PnL helpers
    render.js          # DOM render (topbar, cards, activity, stress)
    sections.js        # dashboard section layout helpers
    charts.js          # Chart.js panels
    chart-vendor.js    # Chart.js / luxon vendor bootstrap
    date-time.js       # timezone / date formatting
    refresh.js         # data refresh + investor load UX
    investor-cache.js  # investor portal localStorage warm cache (portal_cache payloads)
  shared/
    config.js          # constants, formatters, strategies
    context.js         # investor mode, i18n, API base URL
    state.js           # mutable UI state
  tailwind-input.css   # @tailwind source (→ ../tailwind.css)
tailwind.config.js
tokens.css             # design tokens (CSS variables; served as static file)
styles.css             # custom component CSS (non-Tailwind)
app.js                 # operator bundle (npm run build)
app-investor.js        # investor portal bundle
```

Investor portal 會優先使用後端 `portal_snapshots.db` 預組 bundle（`source=portal_cache`）；`investor-cache.js` 在瀏覽器端快取完整 dashboard payload，減少冷啟動等待。後端 scheduler 與 env 見 [`docs/dashboard-zh-TW.md`](../docs/dashboard-zh-TW.md#背景快照與快取)。

## E2E smoke tests (Playwright)

```bash
cd frontend
npm ci
npx playwright install chromium
npm run test:e2e
```

Starts a mocked dashboard via `../scripts/run_e2e_dashboard.py` unless `DASHBOARD_BASE_URL` is set.

HTTP-level smoke tests also run in pytest: `tests/e2e/test_dashboard_http_smoke.py`.
