function payoffAt(kind, price, d) {
  const s = Number(price);
  const spot = Number(d.spot) || 0;
  const strike = Number(d.strike) || 0;
  const premium = Number(d.premium) || 0;
  const shortK = Number(d.short_strike) || 0;
  const longK = Number(d.long_strike) || 0;
  if (kind === "covered_call") {
    return s >= strike ? strike - spot + premium : s - spot + premium;
  }
  if (kind === "spot") {
    return s - spot;
  }
  if (kind === "cash_secured_put") {
    return s >= strike ? premium : s - strike + premium;
  }
  if (kind === "bull_put_spread") {
    const width = shortK - longK;
    if (s >= shortK) return premium;
    if (s <= longK) return premium - width;
    return premium - (shortK - s);
  }
  if (kind === "naked_short_call") {
    return s <= strike ? premium : premium - (s - strike);
  }
  return 0;
}

function fmtChartUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : n > 0 ? "+" : "";
  if (abs >= 1000) {
    return `${sign}$${Math.round(abs).toLocaleString("en-US")}`;
  }
  return `${sign}$${abs.toFixed(0)}`;
}

function fmtAxisUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  if (abs >= 1000) return `${sign}$${Math.round(abs / 1000)}k`;
  return `${sign}$${Math.round(abs)}`;
}

function extremaFor(d) {
  const xMin = Number(d.x_min);
  const xMax = Number(d.x_max);
  const kind = d.kind;
  const samples = [];
  const steps = 40;
  for (let i = 0; i <= steps; i += 1) {
    const x = xMin + ((xMax - xMin) * i) / steps;
    samples.push(payoffAt(kind, x, d));
    if ((d.series || []).some((row) => row.id === "spot")) samples.push(payoffAt("spot", x, d));
  }
  if (d.strike) {
    samples.push(payoffAt(kind, d.strike, d));
    samples.push(payoffAt("spot", d.strike, d));
  }
  if (d.spot) samples.push(payoffAt(kind, d.spot, d), payoffAt("spot", d.spot, d));
  if (d.short_strike) samples.push(payoffAt(kind, d.short_strike, d));
  if (d.long_strike) samples.push(payoffAt(kind, d.long_strike, d));
  let yMin = Math.min(...samples, 0);
  let yMax = Math.max(...samples, 0);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const pad = (yMax - yMin) * 0.12;
  return { xMin, xMax, yMin: yMin - pad, yMax: yMax + pad };
}

function polyline(kind, d, mapX, mapY, cls) {
  const { xMin, xMax } = extremaFor(d);
  const pts = [];
  const steps = 80;
  const marks = [d.spot, d.strike, d.short_strike, d.long_strike, xMin, xMax].filter((v) => v != null);
  const xs = new Set();
  for (let i = 0; i <= steps; i += 1) xs.add(xMin + ((xMax - xMin) * i) / steps);
  marks.forEach((m) => {
    const n = Number(m);
    if (n >= xMin && n <= xMax) xs.add(n);
  });
  [...xs]
    .sort((a, b) => a - b)
    .forEach((x) => {
      pts.push(`${mapX(x).toFixed(1)},${mapY(payoffAt(kind, x, d)).toFixed(1)}`);
    });
  return `<polyline class="${cls}" fill="none" points="${pts.join(" ")}" />`;
}

function payoffSvg(d, cursor) {
  const W = 720;
  const H = 340;
  const left = 58;
  const right = 18;
  const top = 18;
  const bottom = 42;
  const width = W - left - right;
  const height = H - top - bottom;
  const { xMin, xMax, yMin, yMax } = extremaFor(d);
  const mapX = (x) => left + ((x - xMin) / (xMax - xMin)) * width;
  const mapY = (y) => top + (1 - (y - yMin) / (yMax - yMin)) * height;
  const zeroY = Math.min(top + height, Math.max(top, mapY(0)));
  const cursorX = mapX(cursor);
  const strategyY = mapY(payoffAt(d.kind, cursor, d));
  const hasSpot = (d.series || []).some((row) => row.id === "spot");
  const yTicks = [yMin, 0, yMax];
  const xTicks = [xMin, d.spot, d.strike || d.short_strike, xMax].filter((v, i, arr) => v != null && arr.indexOf(v) === i);
  const grid = yTicks
    .map((y) => `<line class="chart-grid" x1="${left}" x2="${left + width}" y1="${mapY(y)}" y2="${mapY(y)}" />`)
    .join("");
  const yLabels = yTicks
    .map((y) => `<text class="chart-label" x="${left - 8}" y="${mapY(y) + 4}" text-anchor="end">${fmtAxisUsd(y)}</text>`)
    .join("");
  const xLabels = xTicks
    .filter((x) => x >= xMin && x <= xMax)
    .map((x) => `<text class="chart-label" x="${mapX(x)}" y="${H - 12}" text-anchor="middle">${fmtAxisUsd(x)}</text>`)
    .join("");
  const strike = d.strike || d.short_strike;
  const strikeLine =
    strike != null
      ? `<line class="chart-ref" x1="${mapX(strike)}" x2="${mapX(strike)}" y1="${top}" y2="${top + height}" />
         <text class="chart-tag" x="${mapX(strike) + 4}" y="${top + 12}">履約價</text>`
      : "";
  const spotLine =
    d.spot != null && hasSpot
      ? `<line class="chart-ref chart-ref--soft" x1="${mapX(d.spot)}" x2="${mapX(d.spot)}" y1="${top}" y2="${top + height}" />
         <text class="chart-tag chart-tag--muted" x="${mapX(d.spot) + 4}" y="${top + 26}">進場現貨</text>`
      : "";
  const longLine =
    d.long_strike != null
      ? `<line class="chart-ref chart-ref--soft" x1="${mapX(d.long_strike)}" x2="${mapX(d.long_strike)}" y1="${top}" y2="${top + height}" />
         <text class="chart-tag chart-tag--muted" x="${mapX(d.long_strike) + 4}" y="${top + 26}">較低履約價</text>`
      : "";
  const spotPoly = hasSpot ? polyline("spot", d, mapX, mapY, "chart-line chart-line--spot") : "";
  const stratPoly = polyline(d.kind, d, mapX, mapY, "chart-line chart-line--strategy");
  const capNote =
    d.kind === "covered_call"
      ? `<text class="chart-tag" x="${mapX(Math.min(xMax - 5000, (d.strike || xMax) + 8000))}" y="${mapY(payoffAt(d.kind, xMax, d)) - 8}">最大獲益（再漲也停在這）</text>`
      : d.kind === "naked_short_call"
        ? `<text class="chart-tag chart-tag--danger" x="${mapX(xMax) - 8}" y="${mapY(payoffAt(d.kind, xMax, d)) + 14}" text-anchor="end">虧損無上限 →</text>`
        : "";
  const lossNote =
    d.kind === "covered_call"
      ? `<text class="chart-tag chart-tag--danger" x="${left + 8}" y="${mapY(payoffAt(d.kind, xMin, d)) - 8}">現貨下跌（還能更低）</text>`
      : "";
  return `<svg class="payoff-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="到期損益圖">
    <line class="chart-axis" x1="${left}" x2="${left}" y1="${top}" y2="${top + height}" />
    <line class="chart-axis" x1="${left}" x2="${left + width}" y1="${zeroY}" y2="${zeroY}" />
    ${grid}
    ${strikeLine}
    ${spotLine}
    ${longLine}
    ${spotPoly}
    ${stratPoly}
    ${capNote}
    ${lossNote}
    ${yLabels}
    ${xLabels}
    <text class="chart-axis-title" x="${left + width / 2}" y="${H - 1}" text-anchor="middle">${escapeHtml(d.x_label_zh || "")}</text>
    <text class="chart-axis-title" x="14" y="${top + height / 2}" text-anchor="middle" transform="rotate(-90 14 ${top + height / 2})">${escapeHtml(d.y_label_zh || "")}</text>
    <line class="chart-cursor" x1="${cursorX}" x2="${cursorX}" y1="${top}" y2="${top + height}" />
    <circle class="chart-dot" cx="${cursorX}" cy="${strategyY}" r="5" />
  </svg>`;
}

function maxMinPayoff(d) {
  const premium = Number(d.premium) || 0;
  if (d.kind === "covered_call") {
    const spot = Number(d.spot) || 0;
    const strike = Number(d.strike) || 0;
    return {
      max: strike - spot + premium,
      min: premium - spot,
      minLabel: `現貨→0 約 ${fmtChartUsd(premium - spot)}`,
    };
  }
  if (d.kind === "cash_secured_put") {
    const strike = Number(d.strike) || 0;
    return { max: premium, min: premium - strike, minLabel: null };
  }
  if (d.kind === "bull_put_spread") {
    const width = Number(d.short_strike) - Number(d.long_strike);
    return { max: premium, min: premium - width, minLabel: null };
  }
  if (d.kind === "naked_short_call") {
    return { max: premium, min: payoffAt(d.kind, d.x_max, d), minLabel: "無上限（圖的右邊還會更低）" };
  }
  const { xMin, xMax } = extremaFor(d);
  const values = [xMin, xMax].map((x) => payoffAt(d.kind, x, d));
  return { max: Math.max(...values), min: Math.min(...values), minLabel: null };
}

function scenarioPnl(d, spotPrice) {
  const strategy = payoffAt(d.kind, spotPrice, d);
  const hasSpot = (d.series || []).some((row) => row.id === "spot");
  return {
    strategy,
    spot: hasSpot ? payoffAt("spot", spotPrice, d) : null,
  };
}

function renderLegend(d) {
  return `<ul class="chart-legend">${(d.series || [])
    .map((row) => `<li class="chart-legend-item chart-legend-item--${escapeHtml(row.id)}">${escapeHtml(row.label_zh)}</li>`)
    .join("")}</ul>`;
}

function renderPieces(pieces) {
  if (!pieces || !pieces.length) return "";
  const cells = pieces.map(
    (p, i) =>
      `${i ? `<span class="anatomy-op" aria-hidden="true">${i === pieces.length - 1 ? "=" : "+"}</span>` : ""}
       <article class="anatomy-piece anatomy-piece--${escapeHtml(p.id)}">
         <h3>${escapeHtml(p.title_zh)}</h3>
         <p>${escapeHtml(p.body_zh)}</p>
       </article>`
  );
  return `<div class="anatomy-row">${cells.join("")}</div>`;
}

function renderFlow(flow) {
  if (!flow || !flow.length) return "";
  return `<ol class="flow-track">${flow
    .map(
      (step, i) => `<li>
        <span class="flow-index">${String(i + 1).padStart(2, "0")}</span>
        <b>${escapeHtml(step.title_zh)}</b>
        <p>${escapeHtml(step.body_zh)}</p>
      </li>`
    )
    .join("")}</ol>`;
}

function renderScenarios(d) {
  return `<div class="scenario-grid">${(d.scenarios || [])
    .map((row) => {
      const pnl = scenarioPnl(d, row.spot);
      const spotLine =
        pnl.spot == null
          ? ""
          : `<div class="scenario-compare">只持有現貨 <span class="font-mono ${pnlClass(pnl.spot)}">${fmtChartUsd(pnl.spot)}</span></div>`;
      return `<article class="scenario-card">
        <p class="scenario-kicker">${escapeHtml(row.label_zh)}</p>
        <p class="scenario-spot font-mono">到期現貨 ${fmtChartUsd(row.spot).replace("+", "")}</p>
        <p class="scenario-pnl font-mono ${pnlClass(pnl.strategy)}">${fmtChartUsd(pnl.strategy)}</p>
        ${spotLine}
        <p class="hint">${escapeHtml(row.caption_zh)}</p>
      </article>`;
    })
    .join("")}</div>`;
}

function renderReadout(d, cursor) {
  const pnl = scenarioPnl(d, cursor);
  const bounds = maxMinPayoff(d);
  const spotLine =
    pnl.spot == null
      ? ""
      : `<div class="readout-compare">若只持有現貨：<strong class="${pnlClass(pnl.spot)}">${fmtChartUsd(pnl.spot)}</strong></div>`;
  return `<div class="chart-readout">
    <div>
      <span class="inv-kpi-label">拖到的到期現貨</span>
      <span class="readout-spot font-mono">${fmtChartUsd(cursor).replace("+", "")}</span>
    </div>
    <div>
      <span class="inv-kpi-label">這筆策略損益</span>
      <span class="readout-pnl font-mono ${pnlClass(pnl.strategy)}">${fmtChartUsd(pnl.strategy)}</span>
      ${spotLine}
    </div>
    <div class="readout-bounds">
      <div><span class="payoff-kicker">圖上最大獲益</span><b class="pnl-pos">${fmtChartUsd(bounds.max)}</b></div>
      <div><span class="payoff-kicker">圖上最大虧損</span><b class="pnl-neg">${bounds.minLabel || fmtChartUsd(bounds.min)}</b></div>
    </div>
  </div>`;
}

function strategyArticleHtml(s, statusLabel) {
  const d = s.diagram;
  if (!d) {
    return `<article class="section-card priority-section"><h2 class="section-title">${escapeHtml(s.name_zh)}</h2><p>${escapeHtml(s.one_liner_zh)}</p></article>`;
  }
  const cursor = Number(d?.spot ?? d?.x_min ?? 0);
  const xMin = Number(d.x_min);
  const xMax = Number(d.x_max);
  const step = Math.max(500, Math.round((xMax - xMin) / 200 / 500) * 500 || 500);
  return `<article class="section-card priority-section strategy-card ${s.available ? "" : "strategy-card--soon"}" data-strategy-id="${escapeHtml(s.id)}">
    <div class="section-heading">
      <div>
        <p class="section-eyebrow">${escapeHtml(s.name_en || "")}</p>
        <h2 class="section-title">${escapeHtml(s.name_zh)}</h2>
      </div>
      <span class="open-meta-pill">${statusLabel[s.status] || s.status}</span>
    </div>
    <p class="section-copy">${escapeHtml(s.one_liner_zh)}</p>
    ${renderPieces(d.pieces)}
    ${renderFlow(d.flow)}
    <section class="chart-panel">
      <div class="section-heading">
        <div>
          <p class="section-eyebrow">Payoff</p>
          <h3 class="section-title">到期損益圖</h3>
        </div>
        ${renderLegend(d)}
      </div>
      <div class="chart-svg-wrap" data-chart></div>
      <label class="chart-slider">
        <span>拖動到期現貨價格</span>
        <input type="range" min="${xMin}" max="${xMax}" step="${step}" value="${cursor}" data-cursor />
      </label>
      <div data-readout>${renderReadout(d, cursor)}</div>
      <p class="hint">${escapeHtml(d.note_zh || "")}</p>
    </section>
    <h3 class="mini-title">三種結局</h3>
    ${renderScenarios(d)}
    <div class="payoff-grid">
      <aside class="payoff payoff--gain">
        <p class="payoff-kicker">${escapeHtml(s.max_profit.title_zh)}</p>
        <h3>${escapeHtml(s.max_profit.headline_zh)}</h3>
      </aside>
      <aside class="payoff payoff--loss">
        <p class="payoff-kicker">${escapeHtml(s.max_loss.title_zh)}</p>
        <h3>${escapeHtml(s.max_loss.headline_zh)}</h3>
      </aside>
    </div>
    <details class="inline-help strategy-more">
      <summary>文字說明、風險與「這不是什麼」</summary>
      <p class="hint">${escapeHtml(s.for_whom_zh || "")}</p>
      <h3 class="mini-title">從零開始</h3>
      <ul class="reason-list">${(s.beginner_zh || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      <h3 class="mini-title">實際怎麼走</h3>
      <ol class="reason-list">${(s.how_it_works_zh || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ol>
      <h3 class="mini-title">風險</h3>
      <ul class="reason-list">${(s.risks_zh || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      <h3 class="mini-title">這不是</h3>
      <ul class="not-list">${(s.not_this_zh || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
    </details>
  </article>`;
}

function paintStrategyChart(article, d, cursor) {
  article.querySelector("[data-chart]").innerHTML = payoffSvg(d, cursor);
  article.querySelector("[data-readout]").innerHTML = renderReadout(d, cursor);
}

function bindStrategyCharts(root, strategies) {
  const byId = Object.fromEntries((strategies || []).map((s) => [s.id, s]));
  root.querySelectorAll("[data-strategy-id]").forEach((article) => {
    const s = byId[article.dataset.strategyId];
    if (!s?.diagram) return;
    const slider = article.querySelector("[data-cursor]");
    const paint = () => paintStrategyChart(article, s.diagram, Number(slider.value));
    slider.addEventListener("input", paint);
    paint();
  });
}
