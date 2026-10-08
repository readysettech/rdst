import { spawnSync } from "node:child_process";

if (process.platform !== "win32") {
  throw new Error("package:win must run on Windows");
}

function run(command, args) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: true
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed`);
  }
}

run("pnpm.cmd", ["prepackage"]);

// The flavor to package (flavors.json); scripts/flavor.mjs renders its config.
const flavor = process.env.RDST_DESKTOP_FLAVOR || "release";
run("node", ["./scripts/flavor.mjs", flavor]);
const builderArgs = ["--win", "--x64", "--publish", "never", "--config", `build/${flavor}/electron-builder.yml`];
// The version the sidecar was stamped with, so the shell and its update metadata match it.
if (process.env.RDST_BUILD_VERSION) {
  builderArgs.push(`-c.extraMetadata.version=${process.env.RDST_BUILD_VERSION}`);
}
run("electron-builder.cmd", builderArgs);
