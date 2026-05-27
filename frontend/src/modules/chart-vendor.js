const CHART_SCRIPTS = [
  "/vendor/luxon.min.js",
  "/vendor/chart.umd.min.js",
  "/vendor/chartjs-adapter-luxon.umd.min.js",
];

let loadPromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded === "true") {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`failed to load ${src}`)), {
        once: true,
      });
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(script);
  });
}

/** Load luxon + Chart.js + adapter on demand when charts section opens. */
export function loadChartJs() {
  if (globalThis.Chart && globalThis.luxon) return Promise.resolve();
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    for (const src of CHART_SCRIPTS) {
      await loadScript(src);
    }
    if (!globalThis.Chart || !globalThis.luxon) {
      throw new Error("Chart.js vendor failed to initialize");
    }
  })().catch((err) => {
    loadPromise = null;
    throw err;
  });
  return loadPromise;
}

export function chartJsReady() {
  return Boolean(globalThis.Chart);
}
