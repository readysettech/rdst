import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import electronBinary from "electron";
import { findSafePython } from "../../../../rdst/scripts/sqlite-runtime.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const appDir = resolve(__dirname, "..");
const webAppsDir = resolve(appDir, "../..");
const rendererDir = resolve(webAppsDir, "apps/rdst");
const rdstDir = resolve(webAppsDir, "../rdst");
const READY_TIMEOUT_MS = 30_000;
const SHUTDOWN_TIMEOUT_MS = 3_000;

// On POSIX, long-running children run detached in their own process groups
// so shutdown() can signal each whole tree; SIGKILL to a lone wrapper PID
// cannot be forwarded and orphans the real Electron/vite/tsup processes.
const detachChildren = process.platform !== "win32";

const children = new Map();
let shuttingDown = false;

// Resolve a dependency's executable JS entrypoint from the package that
// declares it, so children are spawned as direct node processes whose PIDs
// the launcher owns.
function resolveBin(fromDir, packageName, binName) {
  const require = createRequire(resolve(fromDir, "package.json"));
  const packageJsonPath = require.resolve(`${packageName}/package.json`);
  const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf8"));
  const bin =
    typeof packageJson.bin === "string"
      ? packageJson.bin
      : packageJson.bin[binName];
  return resolve(dirname(packageJsonPath), bin);
}

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
  // Detached children live in background process groups, where reading the
  // terminal would stop them with SIGTTIN, so they get no stdin. They also
  // stop receiving terminal-generated SIGINT; the launcher's own signal
  // handlers forward shutdown to them instead.
  const child = spawn(command, args, {
    stdio: detachChildren ? ["ignore", "inherit", "inherit"] : "inherit",
    detached: detachChildren,
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

function killTree(child, signal) {
  if (!child.pid) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    }).once("error", () => {});
    return;
  }
  try {
    process.kill(-child.pid, signal);
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

async function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;

  const running = [...children.values()];
  for (const child of running) {
    killTree(child, "SIGTERM");
  }

  const forceKill = setTimeout(() => {
    for (const child of running) {
      killTree(child, "SIGKILL");
    }
  }, SHUTDOWN_TIMEOUT_MS);

  await Promise.all(running.map(waitForExit));
  clearTimeout(forceKill);
  if (detachChildren) {
    // The tracked children are gone; sweep each process group once more so
    // grandchildren that ignored SIGTERM cannot outlive the launcher.
    for (const child of running) {
      killTree(child, "SIGKILL");
    }
  }
  process.exit(code);
}

async function main() {
  const safePython = findSafePython();
  const backendArgs = [
    "run",
    "--isolated",
    "--python",
    safePython.executable,
    "--directory",
    rdstDir,
    "rdst",
    "web",
    "--ui",
    "none",
    "--reload",
  ];
  console.log(
    `[rdst-desktop] Using ${safePython.executable} ` +
      `(Python ${safePython.python}, SQLite ${safePython.sqlite})`,
  );
  const tsupBinary = resolveBin(appDir, "tsup", "tsup");
  const viteBinary = resolveBin(rendererDir, "vite", "vite");
  console.log("[rdst-desktop] Building Electron main and preload processes...");
  await run(process.execPath, [tsupBinary], { cwd: appDir });

  const backend = start("backend", "uv", backendArgs, { cwd: rdstDir });
  const renderer = start("renderer", process.execPath, [viteBinary, "dev"], {
    cwd: rendererDir,
  });
  start("electron build watcher", process.execPath, [tsupBinary, "--watch"], {
    cwd: appDir,
  });

  const rendererUrl = "http://localhost:3001";
  await Promise.all([
    waitForUrl("RDST backend", "http://localhost:8787/api/init/status", backend),
    waitForUrl("Vite renderer", rendererUrl, renderer),
  ]);

  start("electron", electronBinary, ["."], {
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
