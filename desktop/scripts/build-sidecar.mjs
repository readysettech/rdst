import {
  accessSync,
  chmodSync,
  constants,
  copyFileSync,
  cpSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { findSafePython } from "../../../../rdst/scripts/sqlite-runtime.mjs";

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
const buildRoot = path.resolve(
  process.env.RDST_SIDECAR_BUILD_ROOT ?? path.resolve(rdstDir, "build/electron/pyinstaller")
);
const distRoot = path.resolve(buildRoot, "dist");
const workRoot = path.resolve(buildRoot, "work");
const specRoot = path.resolve(buildRoot, "spec");
const venvRoot = path.resolve(buildRoot, "venv");
const pyinstallerAppDir = path.resolve(distRoot, "rdst");
const lockedRequirements = path.resolve(appDir, "scripts/requirements-sidecar.lock");
const rendererDist = path.resolve(appDir, "out/renderer");
const PYINSTALLER_ARGS = [
  "--onedir",
  "--clean",
  "--noconfirm",
  "--name=rdst",
  // Bundle the rdst dist-info so importlib.metadata can report the version.
  "--copy-metadata=rdst",
  "--collect-all=keyring",
  "--collect-all=fastapi_ai_sdk",
  "--collect-all=psycopg2",
  "--collect-all=paramiko",
  "--collect-all=nacl",
  "--collect-all=bcrypt",
  "--collect-all=cryptography",
  // Slack's socket mode picks a client implementation from whichever of its
  // backends imported, so the choice is only made at run time.
  "--collect-all=slack_bolt",
  "--collect-all=slack_sdk",
  "--collect-submodules=uvicorn",
  "--collect-submodules=sse_starlette",
  "--collect-submodules=sqlglot",
  // Collect RDST's data files (analyze workflows, deploy templates, and qpdemo
  // SQL) that features/shared load relative to __file__.
  "--collect-data=features",
  "--collect-data=shared",
  // RDST resolves some of its own modules through import_module, which the
  // analysis cannot follow, so every first-party module ships whether or not
  // an import statement reaches it.
  "--collect-submodules=features",
  "--collect-submodules=shared",
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

function makeExecutable(filePath) {
  if (process.platform !== "win32") {
    chmodSync(filePath, 0o755);
  }
}

function validateExecutable(filePath) {
  if (!existsSync(filePath) || !statSync(filePath).isFile()) {
    throw new Error(`RDST backend executable not found: ${filePath}`);
  }
  if (process.platform !== "win32") {
    accessSync(filePath, constants.X_OK);
  }
}

function stageSidecarDir(sourceDir) {
  const sourceExecutable = path.resolve(sourceDir, executableName);
  validateExecutable(sourceExecutable);
  rmSync(sidecarAppDir, { recursive: true, force: true });
  mkdirSync(sidecarPlatformDir, { recursive: true });
  // PyInstaller dedups shared libraries with relative symlinks; without
  // verbatimSymlinks, cpSync rewrites their targets to absolute paths
  // into the build tree, which dangle on every other machine.
  cpSync(sourceDir, sidecarAppDir, { recursive: true, verbatimSymlinks: true });
  makeExecutable(stagedExecutable);
  validateExecutable(stagedExecutable);
  console.log(`[rdst-desktop] Staged RDST backend sidecar at ${sidecarAppDir}`);
}

function stageSidecarBinary(sourcePath) {
  validateExecutable(sourcePath);
  rmSync(sidecarAppDir, { recursive: true, force: true });
  mkdirSync(sidecarAppDir, { recursive: true });
  copyFileSync(sourcePath, stagedExecutable);
  makeExecutable(stagedExecutable);
  validateExecutable(stagedExecutable);
  console.log(`[rdst-desktop] Staged RDST backend sidecar at ${stagedExecutable}`);
}

function probeStagedSidecar() {
  const probeHome = mkdtempSync(path.join(tmpdir(), "rdst-sidecar-sqlite-"));
  try {
    run(
      stagedExecutable,
      ["query", "add", "sqlite-runtime-probe", "-q", "SELECT 1"],
      {
        env: {
          HOME: probeHome,
          USERPROFILE: probeHome,
          LOCALAPPDATA: probeHome,
          APPDATA: probeHome,
          RDST_REGISTRY_SQLITE: "1",
          RDST_TESTING: "true"
        }
      }
    );
    const libraryDb = path.resolve(probeHome, ".rdst/library.db");
    if (!existsSync(libraryDb)) {
      throw new Error(
        `Packaged RDST SQLite probe did not create its library at ${libraryDb}`
      );
    }
    console.log("[rdst-desktop] Packaged SQLite WAL safety probe passed");
  } finally {
    rmSync(probeHome, { recursive: true, force: true });
  }
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

function prepareRdstPackageSources() {
  const rendererIndex = path.resolve(rendererDist, "index.html");
  if (!existsSync(rendererIndex)) {
    throw new Error(
      `RDST renderer not found at ${rendererIndex}. Run pnpm build:web before building the sidecar.`
    );
  }

  const rdstWebDist = path.resolve(rdstDir, "web_dist");
  rmSync(rdstWebDist, { recursive: true, force: true });
  cpSync(rendererDist, rdstWebDist, { recursive: true });

  const versionSourcePath = path.resolve(rdstDir, "_version.py");
  const versionBuildPath = path.resolve(rdstDir, "_version_build.py");
  const requestedVersion = process.env.RDST_BUILD_VERSION;

  const versionSource = readFileSync(versionSourcePath, "utf8");
  const major = versionSource.match(/^MAJOR\s*=\s*(\d+)/m)?.[1];
  const minor = versionSource.match(/^MINOR\s*=\s*(\d+)/m)?.[1];
  if (!major || !minor) {
    throw new Error(`Unable to read MAJOR/MINOR from ${versionSourcePath}`);
  }

  const version = requestedVersion ?? `${major}.${minor}.0`;
  writeFileSync(
    versionBuildPath,
    [
      '"""Generated version metadata for an RDST Desktop package build."""',
      "",
      `MAJOR = ${major}`,
      `MINOR = ${minor}`,
      "",
      `__version__ = ${JSON.stringify(version)}`,
      "__version_info__ = (MAJOR, MINOR)",
      ""
    ].join("\n")
  );
}

function buildWithPyInstaller() {
  prepareRdstPackageSources();
  const hostPython = findSafePython({ requiredMinor: "3.12" });
  console.log(
    `[rdst-desktop] Building with ${hostPython.executable} ` +
      `(Python ${hostPython.python}, SQLite ${hostPython.sqlite})`
  );
  const rdstEntry = path.resolve(rdstDir, "rdst.py");
  if (!existsSync(rdstEntry)) {
    throw new Error(`RDST entrypoint not found: ${rdstEntry}`);
  }

  rmSync(buildRoot, { recursive: true, force: true });
  mkdirSync(buildRoot, { recursive: true });
  run(hostPython.executable, ["-m", "venv", venvRoot]);

  const python = venvPythonPath();
  if (!existsSync(lockedRequirements)) {
    throw new Error(`Pinned sidecar requirements not found: ${lockedRequirements}`);
  }
  run(python, [
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "--require-hashes",
    "--requirement",
    lockedRequirements
  ]);
  run(python, [
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "--no-build-isolation",
    "--no-deps",
    "--editable",
    rdstDir
  ]);

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

probeStagedSidecar();
