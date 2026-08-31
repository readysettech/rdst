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
const viteSource = readFileSync(
  resolve(repoLayout().webDir, "vite.config.ts"),
  "utf8",
);

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
      '[viteBinary, "dev", "--port", String(rendererPort), "--strictPort"]',
    );
    expect(desktopSource).toContain(
      'const rendererPort = Number(process.env.RDST_RENDERER_PORT || "3001");',
    );
    expect(desktopSource).toContain(
      'const rendererUrl = `http://localhost:${rendererPort}`;',
    );
  });

  it("owns long-running desktop children directly instead of via wrappers", () => {
    expect(desktopSource).toContain('import electronBinary from "electron";');
    expect(desktopSource).toContain('start("electron", electronBinary, ["."]');
    expect(desktopSource).toContain('resolveBin(appDir, "tsup", "tsup")');
    expect(desktopSource).not.toContain("pnpm");
  });

  it("starts a migrated local Keyservice for desktop development", () => {
    expect(desktopSource).toContain(
      'const keyserviceDir = resolve(rdstDir, "keyservice");',
    );
    expect(desktopSource).toContain(
      'const keyserviceUrl = "http://127.0.0.1:8788";',
    );
    expect(desktopSource).toContain(
      'const backendUrl = "http://127.0.0.1:8787";',
    );
    expect(desktopSource).toContain('"rdst-keyservice-db-local"');
    expect(desktopSource).toContain('"--local"');
    expect(desktopSource).toContain('"DISABLE_SIGNUP_RATE_LIMIT:true"');
    expect(desktopSource).toContain(
      'const keyservice = start(\n    "keyservice",\n    "uv"',
    );
    expect(desktopSource).toContain(
      'env: { RDST_KEYSERVICE_URL: keyserviceUrl }',
    );
    expect(desktopSource).not.toContain("configuredKeyserviceUrl");
    expect(desktopSource).toContain(
      'waitForUrl("Keyservice", `${keyserviceUrl}/health`, keyservice)',
    );
    expect(viteSource).toContain("target: 'http://localhost:8787'");
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
