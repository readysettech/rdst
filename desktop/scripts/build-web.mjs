import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { repoLayout } from "./repo-layout.mjs";

const appDir = path.resolve(import.meta.dirname, "..");
const webAppsRoot = repoLayout().workspaceRoot;
const sourceDist = path.resolve(repoLayout().webDir, "dist");
const targetDist = path.resolve(appDir, "out/renderer");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: webAppsRoot,
    stdio: "inherit",
    shell: process.platform === "win32",
    ...options
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed`);
  }
}

const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
run(pnpmCommand, ["--filter", "rdst-web", "build"]);

if (!existsSync(path.join(sourceDist, "index.html"))) {
  throw new Error(`RDST web build did not produce ${sourceDist}/index.html`);
}

rmSync(targetDist, { recursive: true, force: true });
mkdirSync(targetDist, { recursive: true });
cpSync(sourceDist, targetDist, { recursive: true });

console.log(`[rdst-desktop] Copied RDST web bundle to ${targetDist}`);
