import { build } from "esbuild";
import { execSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

execSync(
  "npx tailwindcss -i src/tailwind-input.css -o tailwind.css --minify",
  { cwd: root, stdio: "inherit" }
);

await build({
  entryPoints: [resolve(root, "src/main.js")],
  outfile: resolve(root, "app.js"),
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: true,
  logLevel: "info",
});
