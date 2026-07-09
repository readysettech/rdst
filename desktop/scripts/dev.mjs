import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const appDir = resolve(__dirname, "..");
const webAppsDir = resolve(appDir, "../..");
const rdstDir = resolve(webAppsDir, "../rdst");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const READY_TIMEOUT_MS = 30_000;
const SHUTDOWN_TIMEOUT_MS = 3_000;

const children = new Map();
let shuttingDown = false;

function run(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      ...options,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolvePromise();
        return;
      }
      reject(
        new Error(
          `${command} ${args.join(" ")} failed (code=${code}, signal=${signal})`,
        ),
      );
    });
  });
}

function start(label, command, args, options = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    ...options,
    env: {
      ...process.env,
      ...(options.env ?? {}),
    },
  });
  children.set(label, child);

  child.once("error", (error) => {
    if (shuttingDown) return;
    console.error(`[rdst-desktop] ${label} failed to start: ${error.message}`);
    void shutdown(1);
  });
  child.once("exit", (code, signal) => {
    children.delete(label);
    if (shuttingDown) return;

    if (label !== "electron" || (code !== 0 && signal == null)) {
      console.error(
        `[rdst-desktop] ${label} exited (code=${code}, signal=${signal})`,
      );
    }
    void shutdown(code ?? (signal ? 0 : 1));
  });

  return child;
}

async function waitForUrl(label, url, child) {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastError = "not checked";

  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`${label} exited before becoming ready`);
    }
    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(1_000),
      });
      if (response.ok) {
        console.log(`[rdst-desktop] ${label} ready at ${url}`);
        return;
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }

  throw new Error(
    `Timed out waiting for ${label} at ${url}. Last error: ${lastError}`,
  );
}

function waitForExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return new Promise((resolvePromise) => child.once("exit", resolvePromise));
}

async function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;

  const running = [...children.values()];
  for (const child of running) {
    child.kill("SIGTERM");
  }

  const forceKill = setTimeout(() => {
    for (const child of running) {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
    }
  }, SHUTDOWN_TIMEOUT_MS);

  await Promise.all(running.map(waitForExit));
  clearTimeout(forceKill);
  process.exit(code);
}

async function main() {
  console.log("[rdst-desktop] Building Electron main and preload processes...");
  await run(pnpm, ["exec", "tsup"], { cwd: appDir });

  const backend = start(
    "backend",
    "uv",
    ["run", "--directory", rdstDir, "rdst", "web", "--ui", "none", "--reload"],
    { cwd: appDir },
  );
  const renderer = start(
    "renderer",
    pnpm,
    ["--filter", "rdst-web", "dev:vite"],
    { cwd: webAppsDir },
  );
  start("electron build watcher", pnpm, ["exec", "tsup", "--watch"], {
    cwd: appDir,
  });

  const rendererUrl = "http://localhost:3001";
  await Promise.all([
    waitForUrl("RDST backend", "http://localhost:8787/api/init/status", backend),
    waitForUrl("Vite renderer", rendererUrl, renderer),
  ]);

  start("electron", pnpm, ["exec", "electron", "."], {
    cwd: appDir,
    env: { ELECTRON_RENDERER_URL: rendererUrl },
  });
}

process.on("SIGINT", () => void shutdown(0));
process.on("SIGTERM", () => void shutdown(0));

main().catch((error) => {
  console.error(`[rdst-desktop] development startup failed: ${error.message}`);
  void shutdown(1);
});
