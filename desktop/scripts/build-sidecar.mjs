import {
  chmodSync,
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  rmSync,
  statSync
} from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

const appDir = path.resolve(import.meta.dirname, "..");
const repoRoot = path.resolve(appDir, "../../..");
const rdstDir = path.resolve(process.env.RDST_SOURCE_DIR ?? path.resolve(repoRoot, "rdst"));
const packageOs =
  process.platform === "darwin"
    ? "mac"
    : process.platform === "win32"
      ? "win"
      : process.platform;
const sidecarPlatformDir = path.resolve(appDir, "sidecar", `${packageOs}-${process.arch}`);
const sidecarAppDir = path.resolve(sidecarPlatformDir, "rdst");
const executableName = process.platform === "win32" ? "rdst.exe" : "rdst";
const stagedExecutable = path.resolve(sidecarAppDir, executableName);
const buildRoot = path.resolve(rdstDir, "build/electron/pyinstaller");
const distRoot = path.resolve(buildRoot, "dist");
const workRoot = path.resolve(buildRoot, "work");
const specRoot = path.resolve(buildRoot, "spec");
const venvRoot = path.resolve(buildRoot, "venv");
const pyinstallerAppDir = path.resolve(distRoot, "rdst");

const PYTHON_CANDIDATES =
  process.platform === "win32"
    ? ["py", "python", "python3"]
    : ["python3.12", "python3.11", "python3.10", "python3", "python"];

const PYINSTALLER_ARGS = [
  "--onedir",
  "--clean",
  "--noconfirm",
  "--name=rdst",
  "--collect-all=keyring",
  "--collect-all=fastapi_ai_sdk",
  "--collect-all=psycopg2",
  "--collect-all=cryptography",
  "--collect-submodules=uvicorn",
  "--collect-submodules=sse_starlette",
  "--collect-submodules=sqlglot",
  "--hidden-import=uvicorn.logging",
  "--hidden-import=uvicorn.loops.auto",
  "--hidden-import=uvicorn.protocols.http.auto",
  "--hidden-import=uvicorn.protocols.websockets.auto",
  "--hidden-import=uvicorn.lifespan.on"
];

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    ...options,
    env: {
      ...process.env,
      ...(options.env ?? {})
    }
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed`);
  }
}

function commandExists(command) {
  return spawnSync(command, ["--version"], { stdio: "ignore" }).status === 0;
}

function detectPython() {
  for (const candidate of PYTHON_CANDIDATES) {
    if (commandExists(candidate)) {
      return candidate;
    }
  }
  throw new Error(`Unable to find Python. Tried: ${PYTHON_CANDIDATES.join(", ")}`);
}

function makeExecutable(filePath) {
  if (process.platform !== "win32") {
    chmodSync(filePath, 0o755);
  }
}

function ensureExecutable(filePath) {
  if (!existsSync(filePath) || !statSync(filePath).isFile()) {
    throw new Error(`RDST backend executable not found: ${filePath}`);
  }
  makeExecutable(filePath);
}

function stageSidecarDir(sourceDir) {
  const sourceExecutable = path.resolve(sourceDir, executableName);
  ensureExecutable(sourceExecutable);
  rmSync(sidecarAppDir, { recursive: true, force: true });
  mkdirSync(sidecarPlatformDir, { recursive: true });
  cpSync(sourceDir, sidecarAppDir, { recursive: true });
  ensureExecutable(stagedExecutable);
  console.log(`[rdst-desktop] Staged RDST backend sidecar at ${sidecarAppDir}`);
}

function stageSidecarBinary(sourcePath) {
  ensureExecutable(sourcePath);
  rmSync(sidecarAppDir, { recursive: true, force: true });
  mkdirSync(sidecarAppDir, { recursive: true });
  copyFileSync(sourcePath, stagedExecutable);
  ensureExecutable(stagedExecutable);
  console.log(`[rdst-desktop] Staged RDST backend sidecar at ${stagedExecutable}`);
}

function addDataArg(source, target) {
  const sourcePath = path.resolve(rdstDir, source);
  if (!existsSync(sourcePath)) return null;
  const separator = process.platform === "win32" ? ";" : ":";
  return `--add-data=${sourcePath}${separator}${target}`;
}

function venvPythonPath() {
  if (process.platform === "win32") {
    return path.resolve(venvRoot, "Scripts/python.exe");
  }
  return path.resolve(venvRoot, "bin/python");
}

function buildWithPyInstaller() {
  const hostPython = detectPython();
  const rdstEntry = path.resolve(rdstDir, "rdst.py");
  if (!existsSync(rdstEntry)) {
    throw new Error(`RDST entrypoint not found: ${rdstEntry}`);
  }

  rmSync(buildRoot, { recursive: true, force: true });
  mkdirSync(buildRoot, { recursive: true });
  run(hostPython, ["-m", "venv", venvRoot]);

  const python = venvPythonPath();
  run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"]);
  run(python, ["-m", "pip", "install", "--disable-pip-version-check", "-e", rdstDir]);
  run(python, ["-m", "pip", "install", "--disable-pip-version-check", "pyinstaller"]);

  mkdirSync(distRoot, { recursive: true });
  mkdirSync(workRoot, { recursive: true });
  mkdirSync(specRoot, { recursive: true });

  const args = [
    "-m",
    "PyInstaller",
    `--distpath=${distRoot}`,
    `--workpath=${workRoot}`,
    `--specpath=${specRoot}`,
    ...PYINSTALLER_ARGS,
    addDataArg("web_dist", "web_dist"),
    rdstEntry
  ].filter(Boolean);

  run(python, args, { cwd: rdstDir });
  stageSidecarDir(pyinstallerAppDir);
}

const explicitDir = process.env.RDST_BACKEND_DIR;
const explicitBinary = process.env.RDST_BACKEND_BINARY;

if (explicitDir) {
  stageSidecarDir(path.resolve(explicitDir));
} else if (explicitBinary) {
  stageSidecarBinary(path.resolve(explicitBinary));
} else {
  buildWithPyInstaller();
}
