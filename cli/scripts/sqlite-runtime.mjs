import { spawnSync } from "node:child_process";

const PROBE_CODE = [
  "import json, sqlite3, sys",
  "print(json.dumps({",
  "  'executable': sys.executable,",
  "  'python': f'{sys.version_info.major}.{sys.version_info.minor}',",
  "  'sqlite': sqlite3.sqlite_version,",
  "  'sqlite_info': list(sqlite3.sqlite_version_info),",
  "}))",
].join("\n");

export function walResetFixed(version) {
  const [major = 0, minor = 0, patch = 0] = version;
  if (major !== 3) return major > 3;
  if (minor > 51 || (minor === 51 && patch >= 3)) return true;
  if (minor === 50 && patch >= 7) return true;
  return minor === 44 && patch >= 6;
}

function candidates(requiredMinor) {
  const result = [];
  if (process.env.RDST_SAFE_PYTHON) {
    result.push({ command: process.env.RDST_SAFE_PYTHON, args: [] });
  }
  if (process.platform === "win32") {
    if (requiredMinor) result.push({ command: "py", args: [`-${requiredMinor}`] });
    result.push(
      { command: "py", args: ["-3.12"] },
      { command: "py", args: ["-3.13"] },
      { command: "python", args: [] },
      { command: "python3", args: [] },
    );
  } else {
    if (requiredMinor) {
      result.push({ command: `python${requiredMinor}`, args: [] });
    }
    result.push(
      { command: "python3.12", args: [] },
      { command: "python3.13", args: [] },
      { command: "python3.14", args: [] },
      { command: "python3", args: [] },
      { command: "python", args: [] },
    );
  }
  return result;
}

export function probePython(candidate) {
  const result = spawnSync(
    candidate.command,
    [...candidate.args, "-c", PROBE_CODE],
    { encoding: "utf8" },
  );
  if (result.status !== 0) return null;
  try {
    return JSON.parse(result.stdout.trim());
  } catch {
    return null;
  }
}

export function findSafePython({ requiredMinor, probe = probePython } = {}) {
  const attempted = [];
  const seen = new Set();
  for (const candidate of candidates(requiredMinor)) {
    const label = [candidate.command, ...candidate.args].join(" ");
    if (seen.has(label)) continue;
    seen.add(label);
    const probeResult = probe(candidate);
    if (!probeResult) {
      attempted.push(`${label} (unavailable)`);
      continue;
    }
    if (requiredMinor && probeResult.python !== requiredMinor) {
      attempted.push(`${label} (Python ${probeResult.python})`);
      continue;
    }
    const [pythonMajor, pythonMinor] = probeResult.python.split(".").map(Number);
    if (pythonMajor !== 3 || pythonMinor < 10) {
      attempted.push(`${label} (unsupported Python ${probeResult.python})`);
      continue;
    }
    if (!walResetFixed(probeResult.sqlite_info)) {
      attempted.push(`${label} (unsafe SQLite ${probeResult.sqlite})`);
      continue;
    }
    return probeResult;
  }

  const pythonRequirement = requiredMinor ? `Python ${requiredMinor} with ` : "";
  throw new Error(
    `RDST requires ${pythonRequirement}SQLite >= 3.51.3 (or fixed ` +
      `backport 3.50.7/3.44.6). Set RDST_SAFE_PYTHON to a compatible ` +
      `interpreter. Tried: ${attempted.join(", ")}`,
  );
}
