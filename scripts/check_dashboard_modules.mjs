import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const modulesDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "frontend", "src", "modules");
const files = ["domain.js", "charts.js", "render.js", "refresh.js"];
const exportsByFile = {};
for (const f of files) {
  const text = fs.readFileSync(path.join(modulesDir, f), "utf8");
  exportsByFile[f] = new Set(
    [...text.matchAll(/^export (?:async )?function (\w+)/gm)].map((m) => m[1])
  );
}
const allExports = new Set(Object.values(exportsByFile).flatMap((s) => [...s]));
const builtins = new Set(
  "if for while switch catch function return new typeof parseInt Math Object Array String Number Boolean Set Map Promise console document window fetch clearTimeout setTimeout setInterval clearInterval requestAnimationFrame ResizeObserver Chart luxon Date JSON Error isFinite undefined null true false Intl".split(
    " "
  )
);
const noise =
  /^(abs|add|get|has|includes|join|keys|map|max|min|on|push|reduce|replace|round|set|sort|sum|toFixed|toLowerCase|toMillis|toUpperCase|from|fromISO|querySelector|querySelectorAll|closest|forEach|toggle|classList|textContent|innerHTML|then|finally|race|catch|open|now|startOf|minus|format|encodeURIComponent|toUTC|toFormat|flatMap|fromEntries|isValid|call|rgb|rgba|all|json|error|async|reset|ceil|worker|fn|repeat|endsWith|match|concat|rows|slice|split|trim|find|filter|values|entries|some|every|test|isArray|isFinite|maxTicksLimit|callback|getOwnPropertyDescriptor|hasOwnProperty|valueOf|toString|bars|SMA|denominator|reliably|group|PnL|APR|snapshot|afterBody|profit|coin|qty|instrument|consider|replaceAll|groups|utc|diff|fromMillis|MTM|toLocal|NumberFormat|performance|setAttribute|views|warn|tones|unit|cards|collateral|debit|equity|multi|addEventListener|remove|appendChild|createElement|getContext|destroy|resize|observe|startsWith|hidden|disabled|checked|dataset|removeAttribute|parentElement|seed|pop|shift|unshift|splice|indexOf|flat|flatMap)$/;

let failed = false;
for (const f of files) {
  const text = fs.readFileSync(path.join(modulesDir, f), "utf8");
  const body = text.replace(/^import[\s\S]*?\n(?=export)/, "");
  const imports = new Set();
  for (const m of text.matchAll(/import \{([^}]+)\} from/g)) {
    for (const part of m[1].split(",")) imports.add(part.trim().split(" as ")[0].trim());
  }
  const local = exportsByFile[f];
  const missing = new Set();
  for (const m of body.matchAll(/\b([A-Za-z_][A-Za-z0-9_]*)\s*\(/g)) {
    const name = m[1];
    if (local.has(name) || imports.has(name) || allExports.has(name) || builtins.has(name)) continue;
    if (noise.test(name)) continue;
    missing.add(name);
  }
  if (missing.size) {
    failed = true;
    console.error(`${f}: possibly undefined calls -> ${[...missing].sort().join(", ")}`);
  }
}
if (failed) process.exit(1);
console.log("module cross-reference check OK");
