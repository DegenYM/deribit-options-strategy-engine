import { build } from "esbuild";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

await build({
  entryPoints: [resolve(root, "src/main.js")],
  outfile: resolve(root, "app.js"),
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  logLevel: "info",
});
