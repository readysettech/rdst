import { describe, expect, it } from "vitest";

import { versionBuildSource } from "./version-build.mjs";

const base = { major: 0, minor: 1, version: "0.1.42" };

describe("versionBuildSource", () => {
  it("bakes the version and no keyservice URL by default", () => {
    const source = versionBuildSource(base);
    expect(source).toContain('__version__ = "0.1.42"');
    expect(source).not.toContain("KEYSERVICE_URL");
  });

  it("bakes the keyservice URL a CL build passes", () => {
    const source = versionBuildSource({
      ...base,
      keyserviceUrl: "https://keyservice-cl16302.readyset.io"
    });
    expect(source).toContain('KEYSERVICE_URL = "https://keyservice-cl16302.readyset.io"');
  });

  it("treats an empty URL as unset", () => {
    expect(versionBuildSource({ ...base, keyserviceUrl: "" })).not.toContain("KEYSERVICE_URL");
  });
});
