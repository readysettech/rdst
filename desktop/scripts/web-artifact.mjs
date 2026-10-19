// The RDST web bundle a main build produces once, keyed for analytics, in the
// build-rdst-web step (rdst/.buildkite/build_rdst_web_artifact.sh). Every
// sidecar of that build embeds this bundle rather than a renderer built on
// its own agent, so the Python package and the desktop apps ship one bundle.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

export const WEB_ARTIFACT_ENV = "RDST_WEB_ARTIFACT";
const REQUIRED_KEYS = ["commit", "frontend_version", "bundle_sha256", "bundle_format", "bundle_root", "built_at"];

// Set to "1" by the pipeline on the steps whose build has the artifact.
export function webArtifactRequested(env = process.env) {
  return env[WEB_ARTIFACT_ENV] === "1";
}

// Where build-rdst-web uploads the bundle, relative to the checkout.
export function webArtifactPaths(pythonDir) {
  const dir = path.resolve(pythonDir, ".buildkite-artifacts/rdst-web");
  return {
    dir,
    archive: path.join(dir, "rdst-web-dist.tar.gz"),
    metadata: path.join(dir, "rdst-web-metadata.json")
  };
}

export function sha256File(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

// The same contract build_package.sh enforces before embedding the bundle.
export function validateWebArtifact({ metadata, archiveSha256, commit }) {
  const missing = REQUIRED_KEYS.filter((key) => !(key in metadata));
  if (missing.length > 0) {
    throw new Error(`RDST web artifact metadata is missing ${missing.join(", ")}`);
  }
  if (metadata.bundle_format !== "tar.gz") {
    throw new Error(`RDST web artifact has unsupported bundle_format ${metadata.bundle_format}`);
  }
  if (commit && metadata.commit !== commit) {
    throw new Error(`RDST web artifact is for commit ${metadata.commit}, this build is ${commit}`);
  }
  if (metadata.bundle_sha256 !== archiveSha256) {
    throw new Error(
      `RDST web artifact checksum mismatch: metadata ${metadata.bundle_sha256}, archive ${archiveSha256}`
    );
  }
}
