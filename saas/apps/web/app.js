const $ = (id) => document.getElementById(id);

let cachedRecommendation = null;
let ackIds = [];
let currentTab = "overview";
let lastMe = null;
let lastProduct = null;
let routing = false;

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function show(id, on) {
  const el = $(id);
  if (el) el.classList.toggle("hidden", !on);
}

function money(plan) {
  return `USD ${plan.price_usd_month} / NT$${plan.price_twd_month.toLocaleString()}`;
}

function fmtPrice(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-US", { maximumFractionDigits: n >= 1000 ? 0 : 2 });
}

function fmtUsd(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n >= 1000 ? 0 : 2 });
}

function fmtNative(value, book) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const code = String(book || "").toUpperCase();
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const abs = Math.abs(n);
  const digits = abs >= 1 ? 3 : abs >= 0.01 ? 4 : 6;
  return `${sign}${abs.toFixed(digits)} ${code}`;
}

function fmtNativeBooks(byBook) {
  const entries = Object.entries(byBook || {}).filter(([, value]) => value != null);
  if (!entries.length) return "";
  return entries.map(([book, value]) => fmtNative(value, book)).join(" · ");
}

function fmtCoinThenUsd(nativeText, usdValue) {
  if (!nativeText) return fmtUsd(usdValue);
  if (usdValue == null || usdValue === "") return nativeText;
  return `${nativeText}<span class="pnl-usd">${fmtUsd(usdValue)} U</span>`;
}

function fmtPct(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", { style: "percent", maximumFractionDigits: 1, minimumFractionDigits: 1 });
}

function fmtDays(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(1)} 天`;
}

function pnlClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "pnl-pos" : "pnl-neg";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setChip(el, text, variant) {
  el.className = `inv-chip inv-chip--${variant}`;
  el.textContent = text;
}

function desiredVariant(desired) {
  if (desired === "live") return "success";
  if (desired === "dry_run") return "info";
  if (desired === "paused") return "warning";
  if (desired === "panic") return "danger";
  return "neutral";
}

function stampRefresh() {
  $("lastRefresh").textContent = `更新 ${new Date().toLocaleTimeString()}`;
}

function pageFromPath() {
  const path = (location.pathname.replace(/\/+$/, "") || "/");
  if (path === "/strategy") return "strategy";
  if (path === "/pricing") return "pricing";
  if (path === "/login" || path === "/signup") return "login";
  if (path === "/app") return "app";
  return "landing";
}

function pageId(name) {
  if (name === "strategy") return "strategyPage";
  if (name === "pricing") return "pricingPage";
  if (name === "login") return "loginPage";
  if (name === "app") return "app";
  return "landing";
}

function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".app-tab").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `tab-${name}`);
  });
}

function applyChrome(page) {
  const loggedIn = Boolean(lastMe);
  const inApp = page === "app";
  $("logoutBtn").classList.toggle("hidden", !loggedIn);
  $("statusCluster").classList.toggle("hidden", !inApp);
  $("headerMarkets").classList.toggle("hidden", !inApp);
  $("lastRefresh").classList.toggle("hidden", !inApp);
  $("refreshNow").classList.toggle("hidden", !inApp);
  $("pageSubtitle").textContent = inApp ? "掩護性買權控制台" : "樹冠罩住你已經持有的現貨。";
  const loginNav = $("loginNav");
  if (loggedIn) {
    loginNav.textContent = "控制台";
    loginNav.setAttribute("href", "/app");
    loginNav.dataset.page = "app";
  } else {
    loginNav.textContent = "登入／試用";
    loginNav.setAttribute("href", "/login");
    loginNav.dataset.page = "login";
  }
  document.querySelectorAll(".site-nav a").forEach((link) => {
    link.classList.toggle("is-active", link.dataset.page === page || (page === "app" && link.dataset.page === "app"));
  });
}

function showPage(name) {
  ["landing", "strategyPage", "pricingPage", "loginPage", "app"].forEach((id) => {
    show(id, id === pageId(name));
  });
  applyChrome(name);
}

function navigate(path, { replace = false } = {}) {
  if (location.pathname !== path) {
    if (replace) history.replaceState({}, "", path);
    else history.pushState({}, "", path);
  }
  return route();
}

async function route() {
  if (routing) return;
  routing = true;
  try {
    let page = pageFromPath();
    if (lastMe && (page === "landing" || page === "login")) {
      history.replaceState({}, "", "/app");
      page = "app";
    }
    if (!lastMe && page === "app") {
      history.replaceState({}, "", "/login");
      page = "login";
    }
    showPage(page);
    if (page === "strategy") await loadStrategies();
    if (page === "pricing") await loadPricing();
    if (page === "app" && lastMe) await refreshApp();
  } finally {
    routing = false;
  }
}

function bindSubscribe(containerId, after) {
  const root = $(containerId);
  if (!root) return;
  root.querySelectorAll("button[data-plan]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/billing/dev-subscribe", {
          method: "POST",
          body: JSON.stringify({ plan_id: btn.dataset.plan }),
        });
        if (after) await after();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

function renderPlanCards(plans, mountId) {
  const root = $(mountId);
  if (!root) return;
  root.innerHTML = plans
    .map(
      (plan) => `<article class="plan${plan.trial_eligible ? " plan--trial" : ""}">
        <b>${escapeHtml(plan.name)}</b>
        <div class="plan-price">${money(plan)}</div>
        <p>${escapeHtml(plan.blurb_zh)}</p>
        <ul class="plan-highlights">${(plan.highlights_zh || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
        ${plan.trial_eligible ? `<small>新帳號試用方案（${lastProduct?.trial_days || 30} 天）</small>` : ""}
        <small>${escapeHtml(plan.disclaimer_zh)}</small>
      </article>`
    )
    .join("");
}

async function loadPricing() {
  const catalog = await api("/api/plans");
  $("pricingLede").textContent = catalog.details_pending_zh || "訂閱費，不是績效費。";
  renderPlanCards(catalog.plans, "pricingCards");
  const cmp = catalog.comparison || { plan_ids: [], rows: [], plan_names: {} };
  const ids = cmp.plan_ids || [];
  const head = `<thead><tr><th>項目</th>${ids.map((id) => `<th>${escapeHtml(cmp.plan_names?.[id] || id)}</th>`).join("")}</tr></thead>`;
  const body = `<tbody>${(cmp.rows || [])
    .map(
      (row) =>
        `<tr><th>${escapeHtml(row.label_zh)}</th>${ids.map((id) => `<td>${escapeHtml(row.cells?.[id] || "—")}</td>`).join("")}</tr>`
    )
    .join("")}</tbody>`;
  $("pricingTable").innerHTML = head + body;
  $("subscribeRow").innerHTML = catalog.plans
    .map((plan) => `<button type="button" class="ds-btn" data-plan="${plan.id}">訂閱 ${plan.name}</button>`)
    .join("");
  bindSubscribe("subscribeRow", refreshApp);
}

async function loadStrategies() {
  const data = await api("/api/strategies");
  $("strategyIntro").textContent = data.intro_zh || "";
  const statusLabel = {
    available: "目前提供",
    coming_soon: "即將推出",
    not_offered: "不提供",
  };
  $("strategyMount").innerHTML =
    data.strategies.map((s) => strategyArticleHtml(s, statusLabel)).join("") +
    `<p class="hint">${escapeHtml(data.disclaimer_zh || "")}</p>`;
  bindStrategyCharts($("strategyMount"), data.strategies);
}

function applyBrand(product) {
  lastProduct = product;
  document.title = `${product.brand} · ${product.gloss_zh}`;
  $("heroEyebrow").textContent = `${product.brand} · ${product.gloss_zh} · Deribit`;
  $("heroTitle").textContent = "在自己的現貨上，自動做掩護性買權。";
  $("heroLede").textContent = `${product.tagline_zh}金鑰留在你的 Deribit 子帳。${product.gloss_zh}是遮蔭，不是屋頂。`;
  $("faqOrigin").textContent = product.origin_zh;
  if (product.waitlist_only) {
    $("loginLede").textContent = "輸入 email 登入或註冊。目前為候補模式：核准前不能綁金鑰或啟動。";
  } else {
    $("loginLede").textContent = `輸入 email。沒有帳號會自動建立，並啟用 ${product.trial_plan_id || "Scout"} 方案 ${product.trial_days || 30} 天試用。`;
  }
}

function renderIntake(schema, product) {
  ackIds = (product.acknowledgements || []).map((item) => item.id);
  const fields = schema.questions
    .map(
      (q) => `<label class="field">
        <span>${q.label_zh}</span>
        <select name="${q.id}" class="ds-select ds-select-block" required>
          <option value="" disabled selected>請選擇</option>
          ${q.options.map((opt) => `<option value="${opt.id}">${opt.label_zh}</option>`).join("")}
        </select>
      </label>`
    )
    .join("");
  const extras = `<div class="ack-box">
      <label class="check"><input type="checkbox" id="wantSweep" /> Profit sweep（Pro／Desk）</label>
      <label class="check"><input type="checkbox" id="wantAlerts" /> Telegram 告警</label>
    </div>`;
  const ackList = (product.acknowledgements || []).map((ack) => `<li>${ack.label_zh}</li>`).join("");
  const acks = `<div class="ack-box">
      <label class="check">
        <input type="checkbox" id="ackAll" required />
        我已閱讀<a class="footer-nav-link" href="/legal/RISK_DISCLOSURE.zh-TW.md">風險揭露</a>，並了解下列事項
      </label>
      <details class="inline-help"><summary>詳細聲明</summary><ul class="not-list">${ackList}</ul></details>
    </div>`;
  $("intakeFields").innerHTML = fields + extras + acks;
  $("setupChecklist").innerHTML = (product.setup_checklist_zh || []).map((item) => `<li>${item}</li>`).join("");
}

function showRecommendation(rec) {
  cachedRecommendation = rec;
  const plan = rec.plan || {};
  $("recommendBody").innerHTML = `
    <div class="stat-grid">
      <div class="stat-tile"><div class="label">方案</div><div class="value">${rec.plan_name || rec.plan_id}</div></div>
      <div class="stat-tile"><div class="label">風險</div><div class="value">${rec.risk_tier}</div></div>
      <div class="stat-tile"><div class="label">標的</div><div class="value">${(rec.coins || []).join(", ")}</div></div>
      <div class="stat-tile"><div class="label">sweep</div><div class="value">${rec.profit_sweep ? "開" : "關"}</div></div>
    </div>
    <p class="section-copy" style="margin-top:0.8rem">${plan.blurb_zh || ""}</p>
    <ul class="reason-list">${(rec.reasons || []).map((line) => `<li>${line}</li>`).join("")}</ul>`;
  show("recommendPanel", true);
}

function readIntakeForm() {
  const fd = new FormData($("intakeForm"));
  if (!$("ackAll").checked) throw new Error("請先勾選風險揭露");
  return {
    experience: fd.get("experience"),
    inventory: fd.get("inventory"),
    coins: fd.get("coins"),
    capital_band: fd.get("capital_band"),
    intent: fd.get("intent"),
    drawdown: fd.get("drawdown"),
    want_sweep: $("wantSweep").checked,
    alerts: $("wantAlerts").checked,
    acknowledgements: ackIds,
  };
}

function fillIntake(answers) {
  if (!answers) return;
  Object.entries(answers).forEach(([key, value]) => {
    const field = $("intakeForm").elements[key];
    if (field && value) field.value = value;
  });
  $("wantSweep").checked = !!answers.want_sweep;
  $("wantAlerts").checked = !!answers.alerts;
  if (lastMe?.intake_complete) $("ackAll").checked = true;
}

function renderNextSteps({ me, bill, bot }) {
  const items = [
    { ok: me.intake_complete, label: "開通設定", tab: "setup" },
    { ok: me.approved, label: "帳號核准", tab: "setup" },
    { ok: Boolean(bill.plan_id), label: "訂閱或試用", tab: "setup" },
    { ok: bot.has_credentials, label: "API 金鑰", tab: "setup" },
    { ok: ["dry_run", "live", "paused"].includes(bot.desired), label: "已啟動", tab: "overview" },
  ];
  const pending = items.filter((item) => !item.ok);
  if (!pending.length) {
    show("nextSteps", false);
    return;
  }
  $("nextSteps").innerHTML = items
    .map(
      (item) =>
        `<button type="button" class="inv-chip ${item.ok ? "inv-chip--success" : "inv-chip--neutral"}" data-goto="${item.tab}">${item.ok ? "完成" : "待辦"} · ${item.label}</button>`
    )
    .join("");
  $("nextSteps").querySelectorAll("button[data-goto]").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.dataset.goto));
  });
  show("nextSteps", true);
}

function renderPerformance(perf, disclaimer) {
  const since = perf?.since ? `自 ${perf.since}` : "尚未有倉位";
  const winHold =
    perf?.win_rate == null && perf?.avg_holding_days == null
      ? "—"
      : `${fmtPct(perf.win_rate)} · ${fmtDays(perf.avg_holding_days)}`;
  const nativePnl = fmtNativeBooks(perf?.lifetime_pnl_native_by_book);
  const nativeCredit = fmtNativeBooks(perf?.open_credit_native_by_book);
  const pnlClassValue =
    Object.values(perf?.lifetime_pnl_native_by_book || {})[0] ?? perf?.lifetime_pnl_usdc;
  $("perfMount").innerHTML = `
    <section class="inv-panel inv-panel--hero" aria-label="投資組合摘要">
      <div class="inv-split">
        <div class="inv-kpi inv-kpi--equity">
          <span class="inv-kpi-label">總權益</span>
          <span class="inv-kpi-value inv-kpi-value--hero font-mono tabular-nums">${fmtUsd(perf?.total_equity_usdc)}</span>
          <span class="inv-kpi-foot">${perf?.has_data ? "子帳權益（引擎上次同步，U 本位）" : "綁定金鑰並啟動後才會有數字"}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">累計獲利</span>
          <span class="inv-kpi-value inv-kpi-value--hero font-mono tabular-nums ${pnlClass(pnlClassValue)}">${fmtCoinThenUsd(nativePnl, perf?.lifetime_pnl_usdc)}</span>
          <span class="inv-kpi-foot">幣本位為主 · APR ${fmtPct(perf?.lifetime_apr)} · 不是收益承諾</span>
        </div>
      </div>
    </section>
    <div class="inv-stat-row">
      <div class="inv-stat">
        <span class="inv-stat-label">未實現權利金</span>
        <span class="inv-stat-value font-mono tabular-nums">${fmtCoinThenUsd(nativeCredit, perf?.open_credit_usdc)}</span>
        <span class="inv-kpi-foot">${perf?.open_count ?? 0} 筆開放中 · 幣本位搭配 U</span>
      </div>
      <div class="inv-stat">
        <span class="inv-stat-label">勝率 · 持倉天數</span>
        <span class="inv-stat-value font-mono tabular-nums">${winHold}</span>
        <span class="inv-kpi-foot">${since} · ${perf?.realized_count ?? 0} 筆已結算</span>
      </div>
    </div>
    <p class="hint">${escapeHtml(perf?.disclaimer_zh || disclaimer || "")}</p>`;
}

function positionCard(g, { closed = false } = {}) {
  const book = String(g.collateral_currency || g.currency || "").toUpperCase();
  const accent = book === "BTC" ? " open-position-card--btc" : "";
  const credit =
    book === "BTC" || book === "ETH"
      ? fmtCoinThenUsd(fmtNative(g.entry_credit, book), g.entry_credit_usdc)
      : fmtUsd(g.entry_credit_usdc ?? g.entry_credit);
  const pnlValue = g.realized_pnl_native ?? g.realized_pnl;
  const pnl = closed
    ? `<span class="${pnlClass(pnlValue)}">${fmtCoinThenUsd(
        g.realized_pnl_native != null ? fmtNative(g.realized_pnl_native, book) : "",
        g.realized_pnl
      )}</span>`
    : "";
  return `<article class="open-position-card${accent}">
    <div class="open-position-header">
      <h3>${escapeHtml(g.short_instrument || "—")}</h3>
      <span class="open-book-pill">${escapeHtml(book || "—")}</span>
      <span class="open-book-pill">${closed ? "已平倉" : "買權"}</span>
    </div>
    <div class="open-position-detail-row">
      <span>數量 ${escapeHtml(g.quantity ?? "—")}</span>
      <span>履約價 ${escapeHtml(g.strike ?? "—")}</span>
      <span>剩餘天數 ${escapeHtml(g.dte_days ?? "—")}</span>
      <span>權利金 ${credit}</span>
      ${pnl}
      ${g.close_reason ? `<span>${escapeHtml(g.close_reason)}</span>` : ""}
    </div>
  </article>`;
}

async function refreshApp() {
  const me = await api("/api/auth/me");
  lastMe = me;
  $("who").textContent = me.email;
  $("waitlistCopy").textContent = `${me.email} 已在候補。核准前可先填開通設定，但不能綁金鑰或啟動。`;
  show("waitlistBanner", !me.approved);
  if (me.trial_active && me.trial_ends_at) {
    const ends = new Date(me.trial_ends_at);
    $("trialCopy").textContent = `${me.plan_id || "Scout"} 試用中，結束於 ${ends.toLocaleDateString()}。`;
    show("trialBanner", true);
  } else {
    show("trialBanner", false);
  }
  applyChrome("app");

  const onboarding = await api("/api/onboarding").catch(() => ({ recommendation: null, answers: null }));
  if (onboarding.answers) fillIntake(onboarding.answers);
  if (onboarding.recommendation) showRecommendation(onboarding.recommendation);

  let dash = { market: null, bot: { open_groups: [], closed_groups: [], performance: {} }, performance: {}, disclaimer: "" };
  let bot = {
    desired: "stopped",
    risk_tier: "low",
    coins: ["BTC"],
    live_unlocked: false,
    has_credentials: false,
    client_id: null,
    secret_last4: "",
    profit_sweep: false,
  };
  let bill = { plan_id: me.plan_id, status: me.subscription_status };
  try {
    dash = await api("/api/dashboard");
    bot = await api("/api/bot/status");
    bill = await api("/api/billing");
  } catch {
    /* waitlist or missing entitlements still render empty KPIs */
  }

  $("billStatus").textContent = bill.plan_id
    ? `目前方案 ${bill.plan_id}（${bill.status}${bill.trial_ends_at ? " · 試用至 " + new Date(bill.trial_ends_at).toLocaleDateString() : ""}）`
    : me.approved
      ? "尚未訂閱。選一個方案即可（開發模式不必走 Stripe）。"
      : "核准後才能訂閱。";

  $("headerSpotBtc").textContent = fmtPrice(dash.market?.btc_usd);
  $("headerSpotEth").textContent = fmtPrice(dash.market?.eth_usd);
  setChip($("whoBadge"), me.email, me.approved ? "success" : "warning");
  const planLabel = me.trial_active ? `${bill.plan_id || me.plan_id} 試用` : bill.plan_id || me.plan_id || "未訂閱";
  setChip($("planBadge"), planLabel, bill.plan_id || me.plan_id ? "info" : "neutral");
  setChip($("desiredBadge"), bot.desired, desiredVariant(bot.desired));
  setChip($("tierBadge"), `風險 · ${bot.risk_tier || "—"}`, "warning");
  setChip($("credsBadge"), bot.has_credentials ? "金鑰已綁" : "未綁金鑰", bot.has_credentials ? "success" : "neutral");

  const perf = dash.performance || dash.bot?.performance || {};
  renderPerformance(perf, dash.disclaimer);

  $("botMeta").innerHTML = [
    ["狀態", bot.desired],
    ["風險", bot.risk_tier],
    ["標的", (bot.coins || []).join(", ") || "—"],
    ["實單解鎖", bot.live_unlocked ? "是" : "否"],
    ["金鑰", bot.client_id ? `${bot.client_id} · ***${bot.secret_last4 || ""}` : "未設定"],
    ["BTC", fmtPrice(dash.market?.btc_usd)],
    ["ETH", fmtPrice(dash.market?.eth_usd)],
  ]
    .map(([label, value]) => `<div class="stat-tile"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");

  const rec = onboarding.recommendation;
  cachedRecommendation = rec;
  const settingsDiffer =
    rec &&
    (rec.risk_tier !== bot.risk_tier ||
      rec.profit_sweep !== !!bot.profit_sweep ||
      (rec.coins || []).join(",") !== (bot.coins || []).join(","));
  show("applyBanner", Boolean(me.approved && rec && settingsDiffer));
  if (rec) $("applyCopy").textContent = `建議 ${rec.plan_id} · ${rec.risk_tier} · ${(rec.coins || []).join(", ")}`;

  const groups = dash.bot.open_groups || [];
  const closed = dash.bot.closed_groups || [];
  $("groupMeta").textContent = `${groups.length} 筆`;
  $("groups").innerHTML = groups.length
    ? groups.map((g) => positionCard(g)).join("")
    : `<div class="open-empty-state">還沒有開放中的掩護性買權。完成設定後在總覽開始模擬。</div>`;
  $("closedGroups").innerHTML = closed.length
    ? closed.map((g) => positionCard(g, { closed: true })).join("")
    : `<div class="open-empty-state">尚無平倉紀錄。</div>`;

  $("keyStatus").textContent = bot.has_credentials ? "已保存（密鑰不回顯）" : "尚未綁定";
  $("riskTier").value = bot.risk_tier || "low";
  document.querySelectorAll("input[name=coin]").forEach((el) => {
    el.checked = (bot.coins || []).includes(el.value);
  });
  $("sweep").checked = !!bot.profit_sweep;
  renderNextSteps({ me, bill, bot });
  if (!me.intake_complete) showTab("setup");
  stampRefresh();
}

document.querySelectorAll(".app-tab").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

document.body.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-page]");
  if (!link) return;
  const url = new URL(link.href, location.origin);
  if (url.origin !== location.origin) return;
  event.preventDefault();
  navigate(url.pathname);
});

window.addEventListener("popstate", () => {
  route().catch(() => {});
});

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const requested = await api("/api/auth/magic-link", {
    method: "POST",
    body: JSON.stringify({ email: $("email").value }),
  });
  $("loginHint").textContent = "開發模式：直接登入中…";
  await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ token: requested.dev_token }) });
  lastMe = await api("/api/auth/me");
  await navigate("/app");
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  lastMe = null;
  location.href = "/";
});

$("refreshNow").addEventListener("click", () =>
  refreshApp().catch(() => {
    lastMe = null;
    navigate("/login");
  })
);

$("intakeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const saved = await api("/api/onboarding", {
      method: "POST",
      body: JSON.stringify(readIntakeForm()),
    });
    $("intakeHint").textContent = "已保存。下面是建議方案。";
    showRecommendation(saved.recommendation);
    await refreshApp();
    showTab("setup");
  } catch (err) {
    $("intakeHint").textContent = err.message;
  }
});

$("applyRecBtn").addEventListener("click", async () => {
  if (!cachedRecommendation) return;
  try {
    await api("/api/bot/settings", {
      method: "POST",
      body: JSON.stringify({
        risk_tier: cachedRecommendation.risk_tier,
        coins: cachedRecommendation.coins,
        profit_sweep: cachedRecommendation.profit_sweep,
      }),
    });
    await refreshApp();
  } catch (err) {
    alert(err.message);
  }
});

$("keyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/bot/credentials", {
      method: "POST",
      body: JSON.stringify({
        client_id: $("clientId").value,
        client_secret: $("clientSecret").value,
      }),
    });
    $("clientSecret").value = "";
    await refreshApp();
  } catch (err) {
    alert(err.message);
  }
});

$("pingBtn").addEventListener("click", async () => {
  try {
    const result = await api("/api/bot/credentials/ping", { method: "POST" });
    alert(result.ok ? "連線成功" : JSON.stringify(result));
  } catch (err) {
    alert(err.message);
  }
});

$("settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const coins = [...document.querySelectorAll("input[name=coin]:checked")].map((el) => el.value);
  try {
    await api("/api/bot/settings", {
      method: "POST",
      body: JSON.stringify({
        risk_tier: $("riskTier").value,
        coins,
        profit_sweep: $("sweep").checked,
      }),
    });
    await refreshApp();
  } catch (err) {
    alert(err.message);
  }
});

async function setDesired(desired) {
  try {
    await api("/api/bot/desired", { method: "POST", body: JSON.stringify({ desired }) });
    await refreshApp();
    showTab("overview");
  } catch (err) {
    alert(err.message);
  }
}

$("dryBtn").addEventListener("click", () => setDesired("dry_run"));
$("liveBtn").addEventListener("click", () => setDesired("live"));
$("stopBtn").addEventListener("click", () => setDesired("stopped"));
$("pauseBtn").addEventListener("click", () => setDesired("paused"));
$("panicBtn").addEventListener("click", () => {
  if (confirm("緊急平倉會嘗試平倉（實單時為真單）。確定？")) setDesired("panic");
});

(async function boot() {
  const [product, schema] = await Promise.all([api("/api/product"), api("/api/onboarding/schema")]);
  applyBrand(product);
  renderIntake(schema, product);
  await loadPricing();
  try {
    lastMe = await api("/api/auth/me");
  } catch {
    lastMe = null;
  }
  await route();
})();
