// A desktop flavor's build facts, from flavors.json.
//
//   node scripts/flavor.mjs <flavor>
//
// Renders electron-builder.yml for the flavor into build/<flavor>/, with the sidecar's
// instance marker beside it, and prints the flavor's RDST_DESKTOP_*=value lines for the CI
// scripts. electron-builder expands its own macros (${version}) in only a few fields and
// never in the identity ones (appId, productName, protocols), so ${RDST_DESKTOP_*} are
// filled in here.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const appDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const flavors = JSON.parse(readFileSync(join(appDir, "flavors.json"), "utf8"));

export function flavorFacts(name, env = process.env) {
  const flavor = Object.hasOwn(flavors, name) ? flavors[name] : undefined;
  if (!flavor) {
    throw new Error(`unknown desktop flavor '${name}': one of ${Object.keys(flavors).join(", ")}`);
  }
  return {
    RDST_DESKTOP_FLAVOR: name,
    RDST_DESKTOP_INSTANCE: flavor.instance,
    RDST_DESKTOP_APP_ID: flavor.appId,
    RDST_DESKTOP_PRODUCT_NAME: flavor.productName,
    RDST_DESKTOP_SCHEME: flavor.scheme,
    RDST_DESKTOP_NAME: flavor.name,
    RDST_DESKTOP_FEED: `https://downloads.readyset.io/packages/${flavor.name}`,
    // A flavor without a keyservice of its own (a change build) uses the one its sidecar was
    // built against, so the shell and the backend agree.
    RDST_DESKTOP_KEYSERVICE:
      flavor.keyservice ?? (env.RDST_BUILD_KEYSERVICE_URL || flavors.staging.keyservice),
    RDST_DESKTOP_UPDATES: flavor.updates,
    RDST_DESKTOP_SIGNED: flavor.signed.join(" "),
    RDST_DESKTOP_BUILDER_CONFIG: `build/${name}/electron-builder.yml`,
    RDST_DESKTOP_MARKER: `build/${name}/instance.json`,
    RDST_DESKTOP_MARKER_KEYSERVICE: instanceMarker(name).keyservice ?? "",
  };
}

// Tells a sidecar launched by something other than the shell which build it is. A fixed
// keyservice rides along because a main build's sidecar, which staging shares, bakes none.
export function instanceMarker(name) {
  const { instance, keyservice } = flavors[name];
  return keyservice ? { instance, keyservice } : { instance };
}

export function renderBuilderConfig(template, facts) {
  return template.replace(/\$\{(RDST_DESKTOP_[A-Z_]+)\}/g, (_, key) => {
    const value = facts[key];
    if (value === undefined) throw new Error(`electron-builder.yml names unknown ${key}`);
    // Values land inside double-quoted YAML scalars.
    if (/["\\\n]/.test(value)) throw new Error(`${key} cannot hold '${value}'`);
    return value;
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const name = process.argv[2];
  const facts = flavorFacts(name);
  const config = join(appDir, facts.RDST_DESKTOP_BUILDER_CONFIG);
  mkdirSync(dirname(config), { recursive: true });
  writeFileSync(
    config,
    renderBuilderConfig(readFileSync(join(appDir, "electron-builder.yml"), "utf8"), facts)
  );
  writeFileSync(join(appDir, facts.RDST_DESKTOP_MARKER), `${JSON.stringify(instanceMarker(name))}\n`);
  for (const [key, value] of Object.entries(facts)) process.stdout.write(`${key}=${value}\n`);
}
