// Stages the RDST web bundle the sidecar embeds, in out/renderer.
//
// A main build stages the bundle its build-rdst-web step produced, keyed for
// analytics, which the pipeline requests with RDST_WEB_ARTIFACT=1; the step
// has to depend on build-rdst-web. Every other build renders the bundle here
// from source, unkeyed.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { repoLayout } from "./repo-layout.mjs";
import {
  WEB_ARTIFACT_ENV,
  sha256File,
  validateWebArtifact,
  webArtifactPaths,
  webArtifactRequested
} from "./web-artifact.mjs";

const appDir = path.resolve(import.meta.dirname, "..");
const { root, workspaceRoot, pythonDir, webDir } = repoLayout();
const sourceDist = path.resolve(webDir, "dist");
const targetDist = path.resolve(appDir, "out/renderer");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: workspaceRoot,
    stdio: "inherit",
    shell: process.platform === "win32",
    ...options
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed`);
  }
}

function buildFromSource() {
  const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
  run(pnpmCommand, ["--filter", "rdst-web", "build"]);
  if (!existsSync(path.join(sourceDist, "index.html"))) {
    throw new Error(`RDST web build did not produce ${sourceDist}/index.html`);
  }
  rmSync(targetDist, { recursive: true, force: true });
  mkdirSync(targetDist, { recursive: true });
  cpSync(sourceDist, targetDist, { recursive: true });
  console.log(`[rdst-desktop] Copied RDST web bundle to ${targetDist}`);
}

// Without a shell, Windows resolves a bare command only with its extension.
const exe = (name) => (process.platform === "win32" ? `${name}.exe` : name);

function stageArtifact() {
  const artifact = webArtifactPaths(pythonDir);
  if (process.env.BUILDKITE_BUILD_ID) {
    const pattern = `${path.relative(root, artifact.dir).split(path.sep).join("/")}/*`;
    run(exe("buildkite-agent"), ["artifact", "download", pattern, root], { cwd: root, shell: false });
  }
  for (const file of [artifact.archive, artifact.metadata]) {
    if (!existsSync(file)) {
      throw new Error(`${WEB_ARTIFACT_ENV}=1 but the build-rdst-web artifact is missing: ${file}`);
    }
  }
  const metadata = JSON.parse(readFileSync(artifact.metadata, "utf8"));
  validateWebArtifact({
    metadata,
    archiveSha256: sha256File(artifact.archive),
    commit: process.env.BUILDKITE_COMMIT ?? ""
  });
  rmSync(targetDist, { recursive: true, force: true });
  mkdirSync(targetDist, { recursive: true });
  run(exe("tar"), ["-xzf", artifact.archive, "-C", targetDist], { shell: false });
  if (!existsSync(path.join(targetDist, "index.html"))) {
    throw new Error(`RDST web artifact has no index.html: ${artifact.archive}`);
  }
  cpSync(artifact.metadata, path.join(targetDist, ".rdst_web_metadata.json"));
  console.log(`[rdst-desktop] Staged RDST web bundle ${metadata.bundle_sha256} from ${artifact.archive}`);
}

if (webArtifactRequested()) {
  stageArtifact();
} else {
  buildFromSource();
}
