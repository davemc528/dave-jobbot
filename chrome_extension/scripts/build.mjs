import { build, context } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";

const watch = process.argv.includes("--watch");
await rm("dist", { recursive: true, force: true });
await mkdir("dist", { recursive: true });
await Promise.all([
  cp("manifest.json", "dist/manifest.json"),
  cp("src/popup/popup.html", "dist/popup.html"),
  cp("src/popup/popup.css", "dist/popup.css")
]);

const options = {
  entryPoints: {
    "service-worker": "src/service-worker.ts",
    "content": "src/content/index.ts",
    "popup": "src/popup/popup.ts"
  },
  bundle: true,
  outdir: "dist",
  format: "esm",
  platform: "browser",
  target: "chrome120",
  sourcemap: true
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
  console.log("Watching extension sources...");
} else {
  await build(options);
}
