// Writes THIRD-PARTY-NOTICES.txt into a packaged app: electron-builder's afterPack hook.
//
// The notices cover what the package holds: the shell's runtime node_modules, the web
// bundle's record (web_dist/third-party.json) and the sidecar's (third-party.json, written by
// sidecar-notices.py) and, for an NSIS installer, what the installer adds; each component with
// the license texts it ships and, where its license asks, where its source is. Electron's own
// licenses stay in LICENSE.electron.txt and LICENSES.chromium.html beside the notices.
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  writeFileSync
} from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { repoLayout } from "./repo-layout.mjs";

export const NOTICES_FILE = "THIRD-PARTY-NOTICES.txt";
const SOURCE_DIR = "third-party-source";
const RECORD_FILE = "third-party.json";
// Electron's licenses, by their packaged name and their name in the Electron distribution.
const ELECTRON_LICENSES = {
  "LICENSE.electron.txt": "LICENSE",
  "LICENSES.chromium.html": "LICENSES.chromium.html"
};
// What an NSIS installer adds beyond the packaged app: the plug-ins it runs while installing
// (electron-builder's nsis-resources 3.4.1) and the elevate helper electron-builder installs into
// resources/. NSIS itself and SpiderBanner are under zlib-style terms, which ask for no notice in
// a binary. Texts are files in licenses/; the LGPL plug-ins' source archives ship with the app.
const NSIS_COMPONENTS = [
  {
    name: "nsis7z (7-Zip NSIS plug-in)",
    version: "19.00",
    license: "LGPL-2.1",
    note:
      "By Nik Medved, Marek Mizanin and Stuart Welch, built on 7-Zip by Igor Pavlov. Its LZMA SDK " +
      "code is in the public domain.",
    // The plug-in package holds the plug-in's own source, which builds on 7-Zip's.
    archives: [
      {
        url: "https://nsis.sourceforge.io/mediawiki/images/6/69/Nsis7z_19.00.7z",
        sha256: "6f2f3730049926f40442ee0c8b7d3e3dee7ace544d82467ff8059ea3f4201c58"
      },
      {
        url: "https://www.7-zip.org/a/7z1900-src.7z",
        sha256: "9ba70a5e8485cf9061b30a2a84fe741de5aeb8dd271aab8889da0e9b3bf1868e"
      }
    ],
    texts: ["lgpl-2.1.txt", "nsis7z.txt"]
  },
  {
    name: "StdUtils (NSIS plug-in)",
    version: "1.14",
    license: "LGPL-2.1",
    note: "Copyright (C) 2004-2018 LoRd_MuldeR.",
    archives: [
      {
        url: "https://github.com/lordmulder/stdutils/releases/download/1.14/StdUtils.2018-10-27.sources.tbz2",
        sha256: "c5e3b1a66219bc9564c2ccc1702691327eecf044ad33404ac268d725d22dca49"
      }
    ],
    texts: ["lgpl-2.1.txt", "stdutils-lgpl-clarification.txt"]
  },
  {
    name: "WinShell (NSIS plug-in)",
    license: "Freeware",
    note: "By Anders, published as freeware on the NSIS wiki: https://nsis.sourceforge.io/WinShell_plug-in",
    texts: []
  },
  {
    name: "Elevate (resources\\elevate.exe)",
    version: "1.0",
    license: "MIT",
    note: "Copyright (C) 2007 Johannes Passing.",
    source: "https://github.com/jpassing/elevate",
    texts: ["elevate.txt"]
  }
];

const appDir = path.resolve(import.meta.dirname, "..");
const licensesDir = path.join(import.meta.dirname, "licenses");
const { packageComponent } = await import(
  pathToFileURL(path.resolve(repoLayout().webDir, "scripts/third-party.mjs")).href
);

function nodeModuleDir(fromDir, name) {
  for (let dir = fromDir; ; dir = path.dirname(dir)) {
    const candidate = path.join(dir, "node_modules", name);
    if (existsSync(path.join(candidate, "package.json"))) return realpathSync(candidate);
    if (path.dirname(dir) === dir) return null;
  }
}

/** The packages electron-builder packs into app.asar: the app's production dependency tree. */
export function shellComponents(rootDir) {
  const found = new Map();
  const visit = (dir) => {
    const pkg = JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"));
    const optional = Object.keys(pkg.optionalDependencies ?? {});
    for (const name of [...Object.keys(pkg.dependencies ?? {}), ...optional]) {
      const depDir = nodeModuleDir(dir, name);
      if (!depDir) {
        if (optional.includes(name)) continue;
        throw new Error(`${pkg.name} depends on ${name}, which is not installed`);
      }
      if (found.has(depDir)) continue;
      found.set(depDir, packageComponent(depDir));
      visit(depDir);
    }
  };
  visit(rootDir);
  return [...found.values()].sort((a, b) => a.name.localeCompare(b.name));
}

const RULE = "-".repeat(80);

function heading(title) {
  return `${title}\n${"=".repeat(title.length)}`;
}

function label(component) {
  return component.version ? `${component.name} ${component.version}` : component.name;
}

function renderComponent(component) {
  const lines = [RULE, label(component), `License: ${component.license}`];
  if (component.source) lines.push(`Source: ${component.source}`);
  if (component.libraries?.length) lines.push(`Includes: ${component.libraries.join(", ")}`);
  if (component.note) lines.push(component.note);
  for (const { file, text } of component.texts) {
    lines.push("", ...(file ? [`[${file}]`] : []), text.trimEnd());
  }
  return lines.join("\n");
}

function wrap(text, width = 80) {
  const lines = [""];
  for (const word of text.split(" ")) {
    const line = lines.at(-1);
    if (line && line.length + word.length >= width) lines.push(word);
    else lines[lines.length - 1] = line ? `${line} ${word}` : word;
  }
  return lines.join("\n");
}

/** The notices text: an index, then every component's license texts, then the project's own. */
export function renderNotices({ productName, project, sections }) {
  const parts = [
    heading(`${productName}: third-party notices`),
    wrap(
      `${productName} is open source software licensed under the ${project.license}; its text ` +
        "is at the end of this file. It includes the third-party software listed below, each " +
        "under its own license. Electron, Chromium and the components they include are covered " +
        `by ${Object.keys(ELECTRON_LICENSES).join(" and ")}, which ship with this file.`
    ),
    wrap("Names and logos of third-party services shown in the app are trademarks of their owners.")
  ];
  for (const { title, components } of sections) {
    parts.push(heading(title), components.map((c) => `  ${label(c)} (${c.license})`).join("\n"));
  }
  for (const { components } of sections) {
    parts.push(...components.map(renderComponent));
  }
  parts.push(
    RULE,
    heading(`${productName} license`),
    ...project.texts.map(({ text }) => text.trimEnd())
  );
  return `${parts.join("\n\n")}\n`;
}

export function nsisComponents() {
  return NSIS_COMPONENTS.map(({ archives = [], ...component }) => ({
    ...component,
    ...(archives.length && {
      source:
        archives.map(({ url }) => `${SOURCE_DIR}/${path.basename(url)}`).join(" and ") +
        ", shipped with the app"
    }),
    texts: component.texts.map((file) => ({
      file,
      text: readFileSync(path.join(licensesDir, file), "utf8")
    }))
  }));
}

/** Downloads the NSIS plug-ins' source archives into dir, checked against their pinned sha256. */
async function shipNsisSources(dir) {
  mkdirSync(dir, { recursive: true });
  for (const { url, sha256 } of NSIS_COMPONENTS.flatMap((c) => c.archives ?? [])) {
    const response = await fetch(url, { signal: AbortSignal.timeout(120_000) });
    if (!response.ok) throw new Error(`Downloading ${url} failed: HTTP ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    const digest = createHash("sha256").update(data).digest("hex");
    if (digest !== sha256) throw new Error(`${url} has sha256 ${digest}, expected ${sha256}`);
    writeFileSync(path.join(dir, path.basename(url)), data);
  }
}

function readRecord(file) {
  if (!existsSync(file)) {
    throw new Error(`Missing third-party record ${file}; rebuild the web bundle and the sidecar`);
  }
  return JSON.parse(readFileSync(file, "utf8"));
}

export async function afterPack(context) {
  const resourcesDir = context.packager.getResourcesDir(context.appOutDir);
  // The sidecar sits beside resources/ (in Contents/ on macOS), the notices beside Electron's
  // licenses on Windows and Linux and in Resources/ on macOS.
  const sidecarDir = path.join(path.dirname(resourcesDir), "sidecar", "rdst");
  const noticesDir = context.electronPlatformName === "darwin" ? resourcesDir : context.appOutDir;

  const sidecar = readRecord(path.join(sidecarDir, RECORD_FILE));
  const web = readRecord(path.join(sidecarDir, "_internal", "web_dist", RECORD_FILE));
  const sections = [
    { title: "Desktop app", components: shellComponents(appDir) },
    { title: "Web interface", components: web.components },
    { title: "Backend", components: sidecar.components }
  ];
  if (context.targets.some((target) => target.name === "nsis")) {
    sections.push({ title: "Windows installer", components: nsisComponents() });
    await shipNsisSources(path.join(noticesDir, SOURCE_DIR));
  }
  const notices = renderNotices({
    productName: context.packager.appInfo.productName,
    project: sidecar.project,
    sections
  });
  writeFileSync(path.join(noticesDir, NOTICES_FILE), notices);
  // electron-builder copies Electron's licenses beside the app on Windows and Linux only.
  const electronDist = path.join(nodeModuleDir(appDir, "electron"), "dist");
  for (const [packaged, source] of Object.entries(ELECTRON_LICENSES)) {
    const target = path.join(noticesDir, packaged);
    if (!existsSync(target)) copyFileSync(path.join(electronDist, source), target);
  }
  console.log(`[rdst-desktop] Wrote ${path.join(noticesDir, NOTICES_FILE)}`);
}
