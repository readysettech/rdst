import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { flavorFacts, instanceMarker, renderBuilderConfig } from "./flavor.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const template = readFileSync(resolve(root, "electron-builder.yml"), "utf8");
const flavors = JSON.parse(readFileSync(resolve(root, "flavors.json"), "utf8"));
const render = (flavor) => renderBuilderConfig(template, flavorFacts(flavor, {}));
const lines = (config, pattern) => config.match(new RegExp(pattern, "gm"));

describe("electron-builder flavors", () => {
  it.each([
    ["release", "", "com.readyset.rdst-desktop", "RDST Desktop", "rdst"],
    ["staging", "staging", "com.readyset.rdst-desktop.staging", "RDST Desktop Staging", "rdst-staging"],
    ["cl", "cl", "com.readyset.rdst-desktop.cl", "RDST Desktop CL", "rdst-cl"],
  ])("gives %s its own identity", (flavor, instance, appId, productName, scheme) => {
    const config = render(flavor);
    const name = `rdst-desktop${instance ? `-${instance}` : ""}`;
    expect(config).toMatch(new RegExp(`^appId: "${appId}"$`, "m"));
    expect(config).toMatch(new RegExp(`^productName: "${productName}"$`, "m"));
    // Exactly one scheme, its own.
    expect(lines(config, "^ {6}- \"rdst[\\w-]*\"$")).toEqual([`      - "${scheme}"`]);
    expect(config).toContain(
      `  name: "${name}"\n  desktopName: "${name}.desktop"\n  rdstInstance: "${instance}"\n`
    );
    expect(config).toContain(`  executableName: "${name}"\n`);
    expect(config).toContain(`  output: "dist/${flavor}"\n`);
    expect(lines(config, "^  artifactName: .*$")).toEqual(
      Array(3).fill(`  artifactName: "${name}-\${version}-\${arch}.\${ext}"`)
    );
    // One update feed per platform, all on this flavor's own channel.
    expect(lines(config, "^ {6}url: .*$")).toEqual(
      ["macos", "windows", "linux"].map(
        (platform) => `      url: "https://downloads.readyset.io/packages/${name}/${platform}/update"`
      )
    );
    // electron-builder's own macros pass through.
    expect(config).toContain('  - from: "sidecar/${os}-${arch}"\n');
    expect(config).toContain(`  - from: "build/${flavor}/instance.json"\n    to: sidecar/rdst/instance.json\n`);
    expect(config).not.toContain("${RDST_");
  });

  it("tells every sidecar which build it is", () => {
    expect(instanceMarker("release")).toEqual({ instance: "" });
    expect(instanceMarker("cl")).toEqual({ instance: "cl" });
    // A main build's sidecar bakes no keyservice, so staging names its own.
    expect(instanceMarker("staging")).toEqual({
      instance: "staging",
      keyservice: "https://keyservice-staging.readyset.io",
    });
  });

  it("names the keyservice a staging marker must carry", () => {
    expect(flavorFacts("staging").RDST_DESKTOP_MARKER_KEYSERVICE).toBe(flavors.staging.keyservice);
    expect(flavorFacts("cl").RDST_DESKTOP_MARKER_KEYSERVICE).toBe("");
  });

  it("points a change build at the keyservice its sidecar was built against", () => {
    const cl = "https://keyservice-cl123.readyset.io";
    expect(flavorFacts("cl", { RDST_BUILD_KEYSERVICE_URL: cl }).RDST_DESKTOP_KEYSERVICE).toBe(cl);
    expect(flavorFacts("cl", {}).RDST_DESKTOP_KEYSERVICE).toBe(flavors.staging.keyservice);
    expect(flavorFacts("staging", { RDST_BUILD_KEYSERVICE_URL: cl }).RDST_DESKTOP_KEYSERVICE).toBe(
      flavors.staging.keyservice
    );
    expect(flavorFacts("release", { RDST_BUILD_KEYSERVICE_URL: cl }).RDST_DESKTOP_KEYSERVICE).toBe("");
  });

  it("refuses unknown flavors and placeholders", () => {
    expect(() => flavorFacts("prod")).toThrow("unknown desktop flavor 'prod'");
    expect(() => flavorFacts("toString")).toThrow("unknown desktop flavor");
    expect(() => renderBuilderConfig("appId: ${RDST_DESKTOP_TYPO}", flavorFacts("release"))).toThrow(
      "unknown RDST_DESKTOP_TYPO"
    );
  });
});
