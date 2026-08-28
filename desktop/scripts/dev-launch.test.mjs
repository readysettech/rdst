import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { repoLayout } from "./repo-layout.mjs";

const webScripts = resolve(repoLayout().webDir, "scripts");
const desktopSource = readFileSync(
  resolve(import.meta.dirname, "dev.mjs"),
  "utf8",
);
const webSource = readFileSync(resolve(webScripts, "dev-full.mjs"), "utf8");

describe("cross-tree path resolution", () => {
  it("keeps both copies of the layout resolver identical", () => {
    expect(readFileSync(resolve(webScripts, "repo-layout.mjs"), "utf8")).toBe(
      readFileSync(resolve(import.meta.dirname, "repo-layout.mjs"), "utf8"),
    );
  });

  it("reaches the Python tree through the resolver, not a relative climb", () => {
    expect(desktopSource).toContain('pythonScriptUrl("sqlite-runtime.mjs")');
    expect(webSource).toContain("pythonScriptUrl('sqlite-runtime.mjs')");
    for (const source of [desktopSource, webSource]) {
      expect(source).not.toContain("../../../../rdst/");
    }
  });
});

describe("development child launchers", () => {
  it("launches the desktop renderer directly from the Vite app directory", () => {
    expect(desktopSource).toContain("const rendererDir = repoLayout().webDir;");
    expect(desktopSource).toContain(
      'resolveBin(rendererDir, "vite", "vite")',
    );
    expect(desktopSource).toContain(
      'start("renderer", process.execPath, [viteBinary, "dev"]',
    );
  });

  it("owns long-running desktop children directly instead of via wrappers", () => {
    expect(desktopSource).toContain('import electronBinary from "electron";');
    expect(desktopSource).toContain('start("electron", electronBinary, ["."]');
    expect(desktopSource).toContain('resolveBin(appDir, "tsup", "tsup")');
    expect(desktopSource).not.toContain("pnpm");
  });

  it("kills whole child process trees on shutdown", () => {
    expect(desktopSource).toContain("detached: detachChildren,");
    expect(desktopSource).toContain("process.kill(-child.pid, signal);");
    expect(desktopSource).toContain(
      '["/pid", String(child.pid), "/T", "/F"]',
    );
    expect(desktopSource).toContain('killTree(child, "SIGTERM");');
    expect(desktopSource).toContain('killTree(child, "SIGKILL");');
  });

  it("uses the same direct Vite process for the web-only launcher", () => {
    expect(webSource).toContain(
      "[...pnpmPrefixArgs, 'exec', 'vite', 'dev']",
    );
    expect(webSource).not.toContain("['run', 'dev:vite']");
  });

  it("keeps the web launcher's Windows cmd wrapper and propagates real child failures", () => {
    expect(webSource).toMatch(/process\.platform === ["']win32["']/);
    expect(webSource).toMatch(
      /\[["']\/d["'], ["']\/s["'], ["']\/c["'], ["']pnpm["']\]/,
    );
    expect(desktopSource).toContain(
      "void shutdown(code ?? (signal ? 0 : 1));",
    );
    expect(webSource).toContain("shutdown(code ?? 1)");
  });
});
