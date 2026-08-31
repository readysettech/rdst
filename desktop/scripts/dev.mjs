import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import electronBinary from "electron";
import { pythonScriptUrl, repoLayout } from "./repo-layout.mjs";

const { findSafePython } = await import(pythonScriptUrl("sqlite-runtime.mjs"));

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const appDir = resolve(__dirname, "..");
const rendererDir = repoLayout().webDir;
const rdstDir = repoLayout().pythonDir;
const keyserviceDir = resolve(rdstDir, "keyservice");
const keyserviceUrl = "http://127.0.0.1:8788";
const backendUrl = "http://127.0.0.1:8787";
const rendererPort = Number(process.env.RDST_RENDERER_PORT || "3001");
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
      env: {
        ...process.env,
        ...(options.env ?? {}),
      },
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
  const startedAt = Date.now();
  child.once("exit", (code, signal) => {
    children.delete(label);
    if (shuttingDown) return;

    if (label !== "electron" || (code !== 0 && signal == null)) {
      console.error(
        `[rdst-desktop] ${label} exited (code=${code}, signal=${signal})`,
      );
    } else if (code === 0 && Date.now() - startedAt < 5_000) {
      console.error(
        "[rdst-desktop] electron exited immediately with code 0 - another " +
          "instance may already hold the single-instance lock. Close it (or " +
          "kill a stale Electron process) and rerun.",
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
  if (!Number.isInteger(rendererPort) || rendererPort < 1 || rendererPort > 65_535) {
    throw new Error(`Invalid RDST_RENDERER_PORT: ${process.env.RDST_RENDERER_PORT}`);
  }
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

  const npxCommand = process.platform === "win32" ? "npx.cmd" : "npx";
  console.log("[rdst-desktop] Applying local Keyservice migrations...");
  await run(
    npxCommand,
    [
      "--yes",
      "wrangler",
      "d1",
      "migrations",
      "apply",
      "rdst-keyservice-db-local",
      "--local",
    ],
    { cwd: keyserviceDir },
  );
  const keyservice = start(
    "keyservice",
    "uv",
    [
      "run",
      "pywrangler",
      "dev",
      "--port",
      "8788",
      "--show-interactive-dev-session=false",
      "--var",
      `SERVICE_URL:${keyserviceUrl}`,
      "--var",
      "DISABLE_SIGNUP_RATE_LIMIT:true",
    ],
    { cwd: keyserviceDir },
  );

  const backend = start("backend", "uv", backendArgs, {
    cwd: rdstDir,
    env: { RDST_KEYSERVICE_URL: keyserviceUrl },
  });
  const renderer = start(
    "renderer",
    process.execPath,
    [viteBinary, "dev", "--port", String(rendererPort), "--strictPort"],
    { cwd: rendererDir },
  );
  start("electron build watcher", process.execPath, [tsupBinary, "--watch"], {
    cwd: appDir,
  });

  const rendererUrl = `http://localhost:${rendererPort}`;
  await Promise.all([
    waitForUrl("RDST backend", `${backendUrl}/api/init/status`, backend),
    waitForUrl("Vite renderer", rendererUrl, renderer),
    waitForUrl("Keyservice", `${keyserviceUrl}/health`, keyservice),
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
