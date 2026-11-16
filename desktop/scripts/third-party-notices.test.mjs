import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { repoLayout } from "./repo-layout.mjs";
import { nsisComponents, renderNotices, shellComponents } from "./third-party-notices.mjs";

const { assetComponents, packageComponent, packageDir } = await import(
  pathToFileURL(path.resolve(repoLayout().webDir, "scripts/third-party.mjs")).href
);

let root;
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), "rdst-notices-"));
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function writePackage(dir, manifest, files = {}) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, "package.json"), JSON.stringify(manifest));
  for (const [name, text] of Object.entries(files)) writeFileSync(path.join(dir, name), text);
}

describe("packageComponent", () => {
  it("keeps every license file the package ships", () => {
    writePackage(
      path.join(root, "a"),
      { name: "a", version: "1.0.0", license: "Apache-2.0" },
      {
        LICENSE: "apache text",
        NOTICE: "notice text",
        "index.js": ""
      }
    );
    expect(packageComponent(path.join(root, "a"))).toEqual({
      name: "a",
      version: "1.0.0",
      license: "Apache-2.0",
      texts: [
        { file: "LICENSE", text: "apache text" },
        { file: "NOTICE", text: "notice text" }
      ]
    });
  });

  it("states the MIT text for an MIT package without a license file", () => {
    writePackage(path.join(root, "b"), {
      name: "b",
      version: "2.0.0",
      license: "MIT",
      author: "Ann <ann@example.com>"
    });
    const component = packageComponent(path.join(root, "b"));
    expect(component.texts[0].text).toContain("Copyright (c) Ann\n");
    expect(component.note).toMatch(/no license file/);
  });

  it("refuses any other license without its text", () => {
    writePackage(path.join(root, "c"), { name: "c", version: "1.0.0", license: "BSD-3-Clause" });
    expect(() => packageComponent(path.join(root, "c"))).toThrow(/ships no license file/);
  });
});

describe("packageDir", () => {
  it.each([
    [
      "/w/node_modules/.pnpm/x@1/node_modules/react/index.js",
      "/w/node_modules/.pnpm/x@1/node_modules/react"
    ],
    ["\0/w/node_modules/@scope/pkg/dist/a.mjs?commonjs-proxy", "/w/node_modules/@scope/pkg"],
    ["C:\\w\\node_modules\\zod\\index.js", "C:/w/node_modules/zod"],
    ["/w/apps/rdst/src/main.tsx", null]
  ])("maps %s", (id, dir) => {
    expect(packageDir(id)).toBe(dir);
  });
});

describe("assetComponents", () => {
  it("names each shipped font and the commercial icon set", () => {
    writePackage(path.join(root, "icons"), { name: "@rs/ui-icons" });
    const fonts = path.join(root, "public/fonts");
    mkdirSync(fonts, { recursive: true });
    for (const file of [
      "Brandface-Regular.ttf",
      "OpenMono-Regular.woff2",
      "OpenMono-Medium.woff2"
    ]) {
      writeFileSync(path.join(fonts, file), "");
    }
    writeFileSync(path.join(fonts, "OFL.txt"), "ofl text");
    writeFileSync(
      path.join(fonts, "licenses.json"),
      JSON.stringify({
        "Open Mono": {
          files: ["OpenMono-Regular.woff2", "OpenMono-Medium.woff2"],
          license: "OFL-1.1",
          text: "OFL.txt"
        },
        Brandface: { files: ["Brandface-Regular.ttf"], license: "Commercial", note: "Licensed." }
      })
    );
    const components = assetComponents(path.join(root, "public"), path.join(root, "icons"));
    expect(components.map((c) => [c.name, c.license])).toEqual([
      ["Brandface", "Commercial"],
      ["Hugeicons Pro", "Commercial"],
      ["Open Mono", "OFL-1.1"]
    ]);
    expect(components[2].texts).toEqual([{ file: "OFL.txt", text: "ofl text" }]);
  });

  it("names the free icon set when it carries its attribution", () => {
    mkdirSync(path.join(root, "icons/svg"), { recursive: true });
    writeFileSync(path.join(root, "icons/svg/ATTRIBUTION.md"), "mit text");
    const [icons] = assetComponents(path.join(root, "public"), path.join(root, "icons"));
    expect(icons).toMatchObject({ name: "Hugeicons (free set)", license: "MIT" });
  });

  it("refuses a font licenses.json does not declare", () => {
    mkdirSync(path.join(root, "public/fonts"), { recursive: true });
    writeFileSync(path.join(root, "public/fonts/Mystery.ttf"), "");
    expect(() => assetComponents(path.join(root, "public"), root)).toThrow(/Mystery.ttf/);
  });
});

describe("shellComponents", () => {
  it("walks the production dependency tree through pnpm's symlinks", () => {
    const store = path.join(root, "node_modules/.pnpm");
    const updater = path.join(store, "updater@1/node_modules/updater");
    writePackage(
      updater,
      { name: "updater", version: "1.0.0", license: "MIT", dependencies: { ms: "2" } },
      { LICENSE: "u" }
    );
    const ms = path.join(store, "ms@2/node_modules/ms");
    writePackage(ms, { name: "ms", version: "2.0.0", license: "MIT" }, { "license.md": "m" });
    symlinkSync(ms, path.join(store, "updater@1/node_modules/ms"));
    const app = path.join(root, "app");
    writePackage(app, {
      name: "app",
      dependencies: { updater: "1" },
      optionalDependencies: { missing: "1" },
      devDependencies: { electron: "1" }
    });
    mkdirSync(path.join(app, "node_modules"));
    symlinkSync(updater, path.join(app, "node_modules/updater"));

    expect(shellComponents(app).map((c) => `${c.name}@${c.version}`)).toEqual([
      "ms@2.0.0",
      "updater@1.0.0"
    ]);
  });
});

describe("renderNotices", () => {
  it("indexes every component, then gives each its texts and the project license last", () => {
    const text = renderNotices({
      productName: "RDST Desktop",
      project: {
        license: "Apache License 2.0",
        texts: [{ file: "LICENSE", text: "APACHE TEXT\n" }]
      },
      sections: [
        {
          title: "Web interface",
          components: [
            {
              name: "react",
              version: "19.0.0",
              license: "MIT",
              texts: [{ file: "LICENSE", text: "react text" }]
            }
          ]
        },
        {
          title: "Backend",
          components: [
            {
              name: "Python",
              version: "3.12.9",
              license: "PSF-2.0",
              libraries: ["libssl.so.3"],
              texts: [{ file: "LICENSE.txt", text: "psf" }]
            },
            {
              name: "Brandface",
              license: "Commercial",
              note: "Used under a commercial license.",
              texts: []
            },
            {
              name: "libfoo",
              version: "1.0",
              license: "LGPL-2.1",
              source: "https://example.com/libfoo-1.0.tar.gz",
              texts: []
            }
          ]
        }
      ]
    });
    expect(text).toMatch(/^RDST Desktop: third-party notices\n=+\n/);
    expect(text).toContain("Web interface\n=============\n\n  react 19.0.0 (MIT)");
    expect(text).toContain("  Python 3.12.9 (PSF-2.0)\n  Brandface (Commercial)");
    expect(text).toContain(
      "Python 3.12.9\nLicense: PSF-2.0\nIncludes: libssl.so.3\n\n[LICENSE.txt]\npsf"
    );
    expect(text).toContain("Brandface\nLicense: Commercial\nUsed under a commercial license.");
    expect(text).toContain(
      "libfoo 1.0\nLicense: LGPL-2.1\nSource: https://example.com/libfoo-1.0.tar.gz"
    );
    expect(text.trimEnd().endsWith("APACHE TEXT")).toBe(true);
  });
});

describe("nsisComponents", () => {
  it("gives each installer component its license texts", () => {
    for (const component of nsisComponents()) {
      for (const { text } of component.texts) expect(text.trim()).not.toBe("");
      if (/LGPL/.test(component.license)) {
        expect(component.source).toMatch(/^third-party-source\/\S+ .*shipped with the app$/);
      }
    }
  });
});
