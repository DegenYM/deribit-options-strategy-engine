const $ = (id) => document.getElementById(id);

let cachedRecommendation = null;
let ackIds = [];
let currentTab = "overview";
let lastMe = null;

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
  $(id).classList.toggle("hidden", !on);
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

function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".app-tab").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `tab-${name}`);
  });
}

function routeScreens(me) {
  const loggedIn = Boolean(me);
  show("landing", !loggedIn);
  show("app", loggedIn);
  $("logoutBtn").classList.toggle("hidden", !loggedIn);
  $("statusCluster").classList.toggle("hidden", !loggedIn);
  $("headerMarkets").classList.toggle("hidden", !loggedIn);
  $("lastRefresh").classList.toggle("hidden", !loggedIn);
  $("refreshNow").classList.toggle("hidden", !loggedIn);
  if (loggedIn) {
    $("pageSubtitle").textContent = "備兌賣 call 控制台";
    show("waitlistBanner", !me.approved);
    if (!me.intake_complete) showTab("setup");
  }
  lastMe = me;
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

async function loadPlans() {
  const { plans } = await api("/api/plans");
  $("plansMount").innerHTML = plans
    .map(
      (plan) => `<article class="plan">
        <b>${plan.name}</b>
        <div class="plan-price">${money(plan)}</div>
        <p>${plan.blurb_zh}</p>
        <small>${plan.disclaimer_zh}</small>
      </article>`
    )
    .join("");
  $("subscribeRow").innerHTML = plans
    .map((plan) => `<button type="button" class="ds-btn" data-plan="${plan.id}">訂閱 ${plan.name}</button>`)
    .join("");
  bindSubscribe("subscribeRow", refreshApp);
}

function applyBrand(product) {
  document.title = `${product.brand} · ${product.gloss_zh}`;
  $("heroEyebrow").textContent = `${product.brand} · ${product.gloss_zh} · Deribit`;
  $("heroTitle").textContent = "在自己的現貨上，自動備兌賣 call。";
  $("heroLede").textContent = `${product.tagline_zh}金鑰留在你的 Deribit 子帳。${product.gloss_zh}是遮蔭，不是屋頂。`;
  $("faqOrigin").textContent = product.origin_zh;
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
    { ok: Boolean(bill.plan_id), label: "訂閱", tab: "setup" },
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

async function refreshApp() {
  const me = await api("/api/auth/me");
  $("who").textContent = me.email;
  $("waitlistCopy").textContent = `${me.email} 已在候補。核准前可先填開通設定，但不能綁金鑰或啟動。`;
  routeScreens(me);

  const onboarding = await api("/api/onboarding").catch(() => ({ recommendation: null, answers: null }));
  if (onboarding.answers) fillIntake(onboarding.answers);
  if (onboarding.recommendation) showRecommendation(onboarding.recommendation);

  let dash = { market: null, bot: { open_groups: [] } };
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
  let bill = { plan_id: null, status: null };
  if (me.approved) {
    dash = await api("/api/dashboard");
    bot = await api("/api/bot/status");
    bill = await api("/api/billing");
  }

  $("billStatus").textContent = bill.plan_id
    ? `目前方案 ${bill.plan_id}（${bill.status}）`
    : me.approved
      ? "尚未訂閱。選一個方案即可（開發模式不必走 Stripe）。"
      : "核准後才能訂閱。";

  $("headerSpotBtc").textContent = fmtPrice(dash.market?.btc_usd);
  $("headerSpotEth").textContent = fmtPrice(dash.market?.eth_usd);
  setChip($("whoBadge"), me.email, me.approved ? "success" : "warning");
  setChip($("planBadge"), bill.plan_id || "未訂閱", bill.plan_id ? "info" : "neutral");
  setChip($("desiredBadge"), bot.desired, desiredVariant(bot.desired));
  setChip($("tierBadge"), `風險 · ${bot.risk_tier || "—"}`, "warning");
  setChip($("credsBadge"), bot.has_credentials ? "金鑰已綁" : "未綁金鑰", bot.has_credentials ? "success" : "neutral");

  $("botMeta").innerHTML = [
    ["狀態", bot.desired],
    ["風險", bot.risk_tier],
    ["標的", (bot.coins || []).join(", ") || "—"],
    ["live 解鎖", bot.live_unlocked ? "是" : "否"],
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
  $("groupMeta").textContent = `${groups.length} 筆`;
  $("groups").innerHTML = groups.length
    ? groups
        .map((g) => {
          const accent = String(g.currency || "").toLowerCase() === "btc" ? " open-position-card--btc" : "";
          return `<article class="open-position-card${accent}">
            <div class="open-position-header">
              <h3>${g.short_instrument}</h3>
              <span class="open-book-pill">${g.currency || "—"}</span>
              <span class="open-book-pill">call</span>
            </div>
            <div class="open-position-detail-row">
              <span>qty ${g.quantity ?? "—"}</span>
              <span>strike ${g.strike ?? "—"}</span>
              <span>DTE ${g.dte_days ?? "—"}</span>
              <span>premium ${g.entry_credit ?? "—"}</span>
            </div>
          </article>`;
        })
        .join("")
    : `<div class="open-empty-state">還沒有開放中的 covered call。完成設定後在總覽啟動 dry-run。</div>`;

  $("keyStatus").textContent = bot.has_credentials ? "已保存（密鑰不回顯）" : "尚未綁定";
  $("riskTier").value = bot.risk_tier || "low";
  document.querySelectorAll("input[name=coin]").forEach((el) => {
    el.checked = (bot.coins || []).includes(el.value);
  });
  $("sweep").checked = !!bot.profit_sweep;
  renderNextSteps({ me, bill, bot });
  stampRefresh();
}

document.querySelectorAll(".app-tab").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const requested = await api("/api/auth/magic-link", {
    method: "POST",
    body: JSON.stringify({ email: $("email").value }),
  });
  $("loginHint").textContent = "開發模式：直接登入中…";
  await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ token: requested.dev_token }) });
  await refreshApp();
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
});

$("refreshNow").addEventListener("click", () => refreshApp().catch(() => {
  $("loginHint").textContent = "尚未登入。";
}));

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
  if (confirm("Panic 會嘗試平倉（實單時為真單）。確定？")) setDesired("panic");
});

(async function boot() {
  const [product, schema] = await Promise.all([api("/api/product"), api("/api/onboarding/schema")]);
  applyBrand(product);
  renderIntake(schema, product);
  await loadPlans();
  try {
    await refreshApp();
  } catch {
    routeScreens(null);
  }
})();
