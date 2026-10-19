import { describe, expect, it } from "vitest";

import { validateWebArtifact, webArtifactRequested } from "./web-artifact.mjs";

const sha = "a".repeat(64);
const metadata = {
  commit: "abc123",
  frontend_version: "0.1.0",
  bundle_sha256: sha,
  bundle_format: "tar.gz",
  bundle_root: ".",
  built_at: "2026-10-19T00:00:00Z"
};

describe("webArtifactRequested", () => {
  it("is on only when the pipeline sets the flag to 1", () => {
    expect(webArtifactRequested({ RDST_WEB_ARTIFACT: "1" })).toBe(true);
    expect(webArtifactRequested({ RDST_WEB_ARTIFACT: "0" })).toBe(false);
    expect(webArtifactRequested({})).toBe(false);
  });
});

describe("validateWebArtifact", () => {
  it("accepts a bundle whose checksum and commit match", () => {
    expect(() => validateWebArtifact({ metadata, archiveSha256: sha, commit: "abc123" })).not.toThrow();
  });

  it("skips the commit check outside Buildkite", () => {
    expect(() => validateWebArtifact({ metadata, archiveSha256: sha, commit: "" })).not.toThrow();
  });

  it("rejects a bundle from another commit", () => {
    expect(() => validateWebArtifact({ metadata, archiveSha256: sha, commit: "def456" })).toThrow(
      /commit abc123, this build is def456/
    );
  });

  it("rejects a checksum mismatch", () => {
    expect(() => validateWebArtifact({ metadata, archiveSha256: "b".repeat(64), commit: "abc123" })).toThrow(
      /checksum mismatch/
    );
  });

  it("rejects missing keys and other formats", () => {
    const { built_at: _omitted, ...partial } = metadata;
    expect(() => validateWebArtifact({ metadata: partial, archiveSha256: sha })).toThrow(/missing built_at/);
    expect(() =>
      validateWebArtifact({ metadata: { ...metadata, bundle_format: "zip" }, archiveSha256: sha })
    ).toThrow(/unsupported bundle_format zip/);
  });
});
