import { build } from "esbuild";
import { execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

const sharedBuildOptions = {
  entryPoints: [resolve(root, "src/main.js")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: true,
  logLevel: "info",
};

execSync(
  "npx tailwindcss -i src/tailwind-input.css -o tailwind.css --minify",
  { cwd: root, stdio: "inherit" }
);

await build({
  ...sharedBuildOptions,
  outfile: resolve(root, "app.js"),
  define: { __BUILD_INVESTOR__: "false" },
});

await build({
  ...sharedBuildOptions,
  outfile: resolve(root, "app-investor.js"),
  define: { __BUILD_INVESTOR__: "true" },
});

// Cache-busting: stamp each asset's `?v=` with a hash of its current content so
// browsers always refetch when (and only when) the file actually changed. This
// avoids the stale-cache mismatch where some users keep old CSS/JS because the
// version string was not bumped manually after editing styles.css / app*.js.
const HASHED_ASSETS = ["app.js", "app-investor.js", "styles.css", "tailwind.css"];
const HTML_FILES = ["index.html", "investor.html", "investor.zh.html"];

const assetVersions = Object.fromEntries(
  HASHED_ASSETS.map((name) => [
    name,
    createHash("sha256").update(readFileSync(resolve(root, name))).digest("hex").slice(0, 10),
  ])
);

for (const file of HTML_FILES) {
  const path = resolve(root, file);
  let html = readFileSync(path, "utf8");
  for (const name of HASHED_ASSETS) {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = new RegExp(`(/${escaped}\\?v=)[^"'#\\s]*`, "g");
    html = html.replace(pattern, `$1${assetVersions[name]}`);
  }
  writeFileSync(path, html);
}

console.log("Stamped asset versions:", assetVersions);
