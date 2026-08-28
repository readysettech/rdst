import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pythonScriptUrl } from "./repo-layout.mjs";

const { findSafePython, walResetFixed } = await import(
  pythonScriptUrl("sqlite-runtime.mjs")
);

describe("SQLite WAL runtime guard", () => {
  it.each([
    [[3, 44, 5], false],
    [[3, 44, 6], true],
    [[3, 47, 1], false],
    [[3, 50, 6], false],
    [[3, 50, 7], true],
    [[3, 51, 2], false],
    [[3, 51, 3], true],
    [[3, 53, 4], true],
  ])("classifies SQLite %s", (version, expected) => {
    expect(walResetFixed(version)).toBe(expected);
  });
});

describe("findSafePython candidate selection", () => {
  // Keep the candidate list independent of the host environment.
  beforeEach(() => vi.stubEnv("RDST_SAFE_PYTHON", ""));
  afterEach(() => vi.unstubAllEnvs());

  const runtime = (python, sqliteInfo) => ({
    executable: `/fake/python${python}`,
    python,
    sqlite: sqliteInfo.join("."),
    sqlite_info: sqliteInfo,
  });

  // Fake probe keyed by version substring so it matches the platform's
  // candidate spelling ("python3.12" on POSIX, "py -3.12" on Windows).
  const probeByVersion = (mapping) => (candidate) => {
    const label = [candidate.command, ...candidate.args].join(" ");
    const key = Object.keys(mapping).find(
      (version) => version !== "default" && label.includes(version),
    );
    if (key) return mapping[key];
    return mapping.default ?? null;
  };

  it("returns the first candidate with a safe interpreter", () => {
    const probe = probeByVersion({ "3.12": runtime("3.12", [3, 51, 3]) });
    expect(findSafePython({ probe }).executable).toBe("/fake/python3.12");
  });

  it("skips interpreters whose SQLite predates the WAL-reset fix", () => {
    const probe = probeByVersion({
      "3.12": runtime("3.12", [3, 37, 2]),
      "3.13": runtime("3.13", [3, 51, 3]),
    });
    expect(findSafePython({ probe }).executable).toBe("/fake/python3.13");
  });

  it("rejects interpreters older than Python 3.10", () => {
    const probe = probeByVersion({ default: runtime("3.9", [3, 53, 4]) });
    expect(() => findSafePython({ probe })).toThrow(/unsupported Python 3\.9/);
  });

  it("honors requiredMinor and reports version mismatches", () => {
    const probe = probeByVersion({ "3.13": runtime("3.13", [3, 51, 3]) });
    expect(findSafePython({ requiredMinor: "3.13", probe }).python).toBe("3.13");
    expect(() => findSafePython({ requiredMinor: "3.14", probe })).toThrow(
      /requires Python 3\.14 .*\(Python 3\.13\)/s,
    );
  });

  it("reports unsafe SQLite versions in the failure diagnostics", () => {
    const probe = probeByVersion({ default: runtime("3.12", [3, 37, 2]) });
    expect(() => findSafePython({ probe })).toThrow(/unsafe SQLite 3\.37\.2/);
  });

  it("directs the user to RDST_SAFE_PYTHON when no candidate resolves", () => {
    expect(() => findSafePython({ probe: () => null })).toThrow(
      /Set RDST_SAFE_PYTHON/,
    );
  });
});

describe("host interpreter probe", () => {
  // Host-capability check, not a unit test: skipped on machines (including CI
  // agents) that have no WAL-safe Python interpreter installed.
  const safeHostRuntime = (() => {
    try {
      return findSafePython();
    } catch {
      return null;
    }
  })();

  it.skipIf(!safeHostRuntime)(
    "finds a genuinely safe host interpreter for desktop development",
    () => {
      expect(safeHostRuntime.python).toMatch(/^3\.(1[0-9]|[2-9][0-9])$/);
      expect(walResetFixed(safeHostRuntime.sqlite_info)).toBe(true);
    },
  );
});
