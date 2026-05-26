/**
 * Adds cross-module imports to dashboard split files.
 * Run: node scripts/fix_dashboard_modules.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const modulesDir = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "frontend",
  "src",
  "modules"
);

const BUILTINS = new Set([
  "Array",
  "Boolean",
  "Chart",
  "Date",
  "DateTime",
  "Error",
  "JSON",
  "Map",
  "Math",
  "Number",
  "Object",
  "Promise",
  "ResizeObserver",
  "Set",
  "String",
  "console",
  "clearTimeout",
  "document",
  "fetch",
  "isFinite",
  "luxon",
  "parseInt",
  "requestAnimationFrame",
  "setInterval",
  "setTimeout",
  "window",
]);

function extractExports(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  const names = new Set();
  for (const re of [/^export function (\w+)/gm, /^export async function (\w+)/gm, /^export let (\w+)/gm]) {
    let m;
    while ((m = re.exec(text))) names.add(m[1]);
  }
  return names;
}

function extractCalls(text, localExports) {
  const calls = new Set();
  const re = /\b([A-Za-z_][A-Za-z0-9_]*)\s*\(/g;
  let m;
  while ((m = re.exec(text))) {
    const name = m[1];
    if (localExports.has(name) || BUILTINS.has(name)) continue;
    calls.add(name);
  }
  return calls;
}

function stripImportBlock(text) {
  const match = text.match(/^((?:import[\s\S]*?from\s+["'][^"']+["'];\n)+)/);
  if (!match) return { imports: "", body: text };
  return { imports: match[1], body: text.slice(match[0].length) };
}

const domainExports = extractExports(path.join(modulesDir, "domain.js"));
const chartsExports = extractExports(path.join(modulesDir, "charts.js"));
const renderExports = extractExports(path.join(modulesDir, "render.js"));
const refreshExports = extractExports(path.join(modulesDir, "refresh.js"));

const allKnown = new Set([...domainExports, ...chartsExports, ...renderExports, ...refreshExports, ...BUILTINS]);

function fixModule(fileName, localExports, importSpecs) {
  const filePath = path.join(modulesDir, fileName);
  let { imports, body } = stripImportBlock(fs.readFileSync(filePath, "utf8"));
  const calls = extractCalls(body, localExports);
  const lines = [];
  for (const [mod, exportSet] of importSpecs) {
    const needed = [...calls].filter((n) => exportSet.has(n)).sort();
    if (needed.length) lines.push(`import { ${needed.join(", ")} } from "./${mod}";`);
  }
  const crossImports = lines.join("\n") + (lines.length ? "\n" : "");
  fs.writeFileSync(filePath, imports + crossImports + body, "utf8");
  console.log(fileName, "added:", lines.map((l) => l.slice(0, 80)).join(" | ") || "(none)");
}

fixModule("charts.js", chartsExports, [
  ["domain.js", domainExports],
]);
fixModule("render.js", renderExports, [
  ["domain.js", domainExports],
  ["charts.js", chartsExports],
]);
fixModule("refresh.js", refreshExports, [
  ["domain.js", domainExports],
  ["charts.js", chartsExports],
  ["render.js", renderExports],
]);

// refresh.js: renderDashboard is injected via options — remove from auto-import if added
let refreshText = fs.readFileSync(path.join(modulesDir, "refresh.js"), "utf8");
refreshText = refreshText.replace(/import \{[^}]*renderDashboard[^}]*\} from "\.\/render\.js";\n?/, "");
fs.writeFileSync(path.join(modulesDir, "refresh.js"), refreshText);

// Patch refresh.js to use renderDashboard from options parameter
const refreshPath = path.join(modulesDir, "refresh.js");
let refresh = fs.readFileSync(refreshPath, "utf8");

if (!refresh.includes("renderDashboardFn")) {
  refresh = refresh.replace(
    /export async function refreshAll\(\{ force = false, silentIfLimited = false \} = \{\}\)/,
    "export async function refreshAll({ force = false, silentIfLimited = false, renderDashboard: renderDashboardFn } = {})"
  );
  refresh = refresh.replace(/\brenderDashboard\(\)/g, "renderDashboardFn?.()");
  fs.writeFileSync(refreshPath, refresh);
}

console.log("done");
