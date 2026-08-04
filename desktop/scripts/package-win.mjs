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

const builderArgs = ["--win", "--x64", "--publish", "never"];
if (process.env.RDST_BUILD_VERSION) {
  builderArgs.push(
    `-c.extraMetadata.version=${process.env.RDST_BUILD_VERSION}`,
    `-c.extraMetadata.rdstUpdates=${process.env.RDST_DESKTOP_UPDATES ?? "disabled"}`
  );
}
run("electron-builder.cmd", builderArgs);
