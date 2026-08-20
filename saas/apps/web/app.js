const $ = (id) => document.getElementById(id);

let cachedRecommendation = null;

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
  const now = new Date();
  $("lastRefresh").textContent = `上次更新：${now.toLocaleTimeString()}`;
}

function setStep(current) {
  [
    ["stepChip1", 1],
    ["stepChip2", 2],
    ["stepChip3", 3],
  ].forEach(([id, n]) => {
    $(id).className = n === current ? "inv-chip inv-chip--info" : n < current ? "inv-chip inv-chip--success" : "inv-chip inv-chip--neutral";
  });
}

function routeScreens(me) {
  const loggedIn = Boolean(me);
  show("landing", !loggedIn);
  show("onboarding", loggedIn && !me.intake_complete);
  show("waitlist", loggedIn && me.intake_complete && !me.approved);
  show("app", loggedIn && me.intake_complete && me.approved);
  $("logoutBtn").classList.toggle("hidden", !loggedIn);
  $("statusCluster").classList.toggle("hidden", !(loggedIn && me.approved && me.intake_complete));
  $("headerMarkets").classList.toggle("hidden", !(loggedIn && me.approved && me.intake_complete));
  $("lastRefresh").classList.toggle("hidden", !(loggedIn && me.approved && me.intake_complete));
  $("refreshNow").classList.toggle("hidden", !(loggedIn && me.approved && me.intake_complete));
  if (loggedIn && !me.intake_complete) setStep(1);
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

function planButtons(plans) {
  return plans
    .map((plan) => `<button type="button" class="ds-btn" data-plan="${plan.id}">訂閱 ${plan.name}</button>`)
    .join("");
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
  $("subscribeRow").innerHTML = planButtons(plans);
  $("recommendSubscribe").innerHTML = planButtons(plans);
  bindSubscribe("subscribeRow", refreshApp);
  bindSubscribe("recommendSubscribe", async () => {
    const bill = await api("/api/billing");
    $("recommendBill").textContent = bill.plan_id
      ? `已開通 ${bill.plan_id}（${bill.status}）`
      : "尚未訂閱。";
  });
}

function applyBrand(product) {
  document.title = `${product.brand} · ${product.gloss_zh}`;
  $("pageSubtitle").textContent = `${product.tagline_zh}不是代操、不是屋頂。`;
  $("heroEyebrow").textContent = `${product.brand} · ${product.gloss_zh}`;
  $("heroTitle").textContent = product.hero_title_zh;
  $("heroLede").textContent = product.origin_zh;
  $("faqOrigin").textContent = product.origin_zh;
  $("whyPoints").innerHTML = (product.why_points_zh || []).map((line) => `<li>${line}</li>`).join("");
}

function renderIntake(schema, product) {
  const questions = schema.questions
    .map(
      (q) => `<fieldset class="choice-set">
        <legend>${q.label_zh}</legend>
        ${q.options
          .map(
            (opt) => `<label class="choice-card">
              <input type="radio" name="${q.id}" value="${opt.id}" required />
              <span>${opt.label_zh}</span>
            </label>`
          )
          .join("")}
      </fieldset>`
    )
    .join("");
  const extras = `<label class="check"><input type="checkbox" id="wantSweep" /> 我想用 profit sweep（只有 Pro／Desk）</label>
    <label class="check"><input type="checkbox" id="wantAlerts" /> 之後要 Telegram 告警</label>
    <p class="section-eyebrow">請全部勾選</p>`;
  const acks = product.acknowledgements
    .map(
      (ack) =>
        `<label class="check ack"><input type="checkbox" name="ack" value="${ack.id}" required /> ${ack.label_zh}</label>`
    )
    .join("");
  $("intakeFields").innerHTML = questions + extras + acks;
  $("setupChecklist").innerHTML = product.setup_checklist_zh.map((item) => `<li>${item}</li>`).join("");
}

function showRecommendation(rec) {
  cachedRecommendation = rec;
  const plan = rec.plan || {};
  $("recommendBody").innerHTML = `
    <p class="section-eyebrow">${rec.plan_name || rec.plan_id}</p>
    <p class="hero-lede" style="margin:0">${plan.blurb_zh || ""}</p>
    <div class="stat-grid">
      <div class="stat-tile"><div class="label">方案</div><div class="value">${rec.plan_id}</div></div>
      <div class="stat-tile"><div class="label">risk tier</div><div class="value">${rec.risk_tier}</div></div>
      <div class="stat-tile"><div class="label">coins</div><div class="value">${(rec.coins || []).join(", ")}</div></div>
      <div class="stat-tile"><div class="label">sweep</div><div class="value">${rec.profit_sweep ? "是" : "否"}</div></div>
    </div>
    <ul class="reason-list">${(rec.reasons || []).map((line) => `<li>${line}</li>`).join("")}</ul>`;
  show("recommendPanel", true);
  setStep(2);
}

function readIntakeForm() {
  const fd = new FormData($("intakeForm"));
  const acks = [...document.querySelectorAll("input[name=ack]:checked")].map((el) => el.value);
  return {
    experience: fd.get("experience"),
    inventory: fd.get("inventory"),
    coins: fd.get("coins"),
    capital_band: fd.get("capital_band"),
    intent: fd.get("intent"),
    drawdown: fd.get("drawdown"),
    want_sweep: $("wantSweep").checked,
    alerts: $("wantAlerts").checked,
    acknowledgements: acks,
  };
}

async function refreshApp() {
  const me = await api("/api/auth/me");
  $("who").textContent = `${me.email} · ${me.approved ? "已核准" : "waitlist"}`;
  $("waitlistCopy").textContent = `${me.email} 的調查已保存。核准後才能綁定 API、訂閱與啟動 dry-run。`;
  routeScreens(me);
  if (!me.approved || !me.intake_complete) return;

  const dash = await api("/api/dashboard");
  const bot = await api("/api/bot/status");
  const bill = await api("/api/billing");
  const onboarding = await api("/api/onboarding");
  cachedRecommendation = onboarding.recommendation;
  $("billStatus").textContent = bill.plan_id
    ? `目前方案 ${bill.plan_id}（${bill.status}）`
    : "尚未訂閱。開發模式可用下方按鈕開通。";

  const btc = dash.market?.btc_usd;
  const eth = dash.market?.eth_usd;
  $("headerSpotBtc").textContent = fmtPrice(btc);
  $("headerSpotEth").textContent = fmtPrice(eth);

  setChip($("whoBadge"), `帳戶 · ${me.email}`, me.approved ? "success" : "warning");
  setChip($("planBadge"), `方案 · ${bill.plan_id || "未訂閱"}`, bill.plan_id ? "info" : "neutral");
  setChip($("desiredBadge"), `狀態 · ${bot.desired}`, desiredVariant(bot.desired));
  setChip($("tierBadge"), `Risk · ${bot.risk_tier || "—"}`, "warning");
  setChip($("credsBadge"), `金鑰 · ${bot.has_credentials ? "已綁定" : "未設定"}`, bot.has_credentials ? "success" : "neutral");

  $("botMeta").innerHTML = [
    ["desired", bot.desired],
    ["tier", bot.risk_tier],
    ["coins", (bot.coins || []).join(", ") || "—"],
    ["live 解鎖", bot.live_unlocked ? "是" : "否"],
    ["金鑰", bot.client_id ? `${bot.client_id} · ***${bot.secret_last4 || ""}` : "未設定"],
    ["現貨 BTC", fmtPrice(btc)],
    ["現貨 ETH", fmtPrice(eth)],
  ]
    .map(([label, value]) => `<div class="stat-tile"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");

  const rec = onboarding.recommendation;
  const settingsDiffer =
    rec &&
    (rec.risk_tier !== bot.risk_tier ||
      rec.profit_sweep !== !!bot.profit_sweep ||
      (rec.coins || []).join(",") !== (bot.coins || []).join(","));
  show("applyBanner", Boolean(rec && settingsDiffer));
  if (rec) {
    $("applyCopy").textContent = `調查建議 ${rec.plan_id} · ${rec.risk_tier} · ${(rec.coins || []).join(", ")}`;
  }

  const groups = dash.bot.open_groups || [];
  $("groupMeta").textContent = `${groups.length} open`;
  $("groups").innerHTML = groups.length
    ? groups
        .map((g) => {
          const book = String(g.currency || "").toLowerCase();
          const accent = book === "btc" ? " open-position-card--btc" : "";
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
    : `<div class="open-empty-state">目前沒有開放中的 covered call。</div>`;
  $("keyStatus").textContent = bot.has_credentials ? "已保存（密鑰不回顯）" : "尚未綁定";
  $("riskTier").value = bot.risk_tier || "low";
  document.querySelectorAll("input[name=coin]").forEach((el) => {
    el.checked = (bot.coins || []).includes(el.value);
  });
  $("sweep").checked = !!bot.profit_sweep;
  stampRefresh();
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const requested = await api("/api/auth/magic-link", {
    method: "POST",
    body: JSON.stringify({ email: $("email").value }),
  });
  $("loginHint").textContent = "開發模式：正在使用回傳的 magic token 登入…";
  await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ token: requested.dev_token }) });
  $("logoutBtn").classList.remove("hidden");
  await refreshApp();
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
});

$("refreshNow").addEventListener("click", async () => {
  try {
    await refreshApp();
  } catch {
    $("loginHint").textContent = "尚未登入。";
  }
});

$("intakeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const saved = await api("/api/onboarding", {
      method: "POST",
      body: JSON.stringify(readIntakeForm()),
    });
    $("intakeHint").textContent = "調查已保存。";
    showRecommendation(saved.recommendation);
  } catch (err) {
    $("intakeHint").textContent = err.message;
  }
});

$("toChecklistBtn").addEventListener("click", () => {
  show("setupPanel", true);
  setStep(3);
  $("setupPanel").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("enterAppBtn").addEventListener("click", async () => {
  await refreshApp();
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
    alert(result.ok ? "Deribit ping 成功" : JSON.stringify(result));
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
  } catch (err) {
    alert(err.message);
  }
}

$("dryBtn").addEventListener("click", () => setDesired("dry_run"));
$("liveBtn").addEventListener("click", () => setDesired("live"));
$("stopBtn").addEventListener("click", () => setDesired("stopped"));
$("pauseBtn").addEventListener("click", () => setDesired("paused"));
$("panicBtn").addEventListener("click", () => {
  if (confirm("Panic 會對該帳戶嘗試平倉（實單時為真單）。確定？")) setDesired("panic");
});

(async function boot() {
  const [product, schema] = await Promise.all([api("/api/product"), api("/api/onboarding/schema")]);
  applyBrand(product);
  renderIntake(schema, product);
  await loadPlans();
  try {
    $("logoutBtn").classList.remove("hidden");
    await refreshApp();
  } catch {
    routeScreens(null);
  }
})();
