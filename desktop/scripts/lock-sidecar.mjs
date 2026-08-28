/**
 * Recompiles `scripts/requirements-sidecar.lock` from the Python tree's
 * `pyproject.toml` plus `scripts/requirements-sidecar.in`.
 *
 * uv records the source path it was given in the lockfile's header and in its
 * `# via rdst (...)` comments, so the tree is passed as a path relative to this
 * app rather than an absolute one: the lockfile stays reproducible on any
 * machine, and each layout spells it the way a reader of that checkout would.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { repoLayout } from "./repo-layout.mjs";

const appDir = path.resolve(import.meta.dirname, "..");
const pyproject = path.posix.join(
  path.relative(appDir, repoLayout().pythonDir).split(path.sep).join("/"),
  "pyproject.toml",
);

const result = spawnSync(
  "uv",
  [
    "pip",
    "compile",
    pyproject,
    "scripts/requirements-sidecar.in",
    "--python-version",
    "3.12",
    "--universal",
    "--generate-hashes",
    "--no-emit-index-url",
    "--output-file",
    "scripts/requirements-sidecar.lock",
  ],
  { cwd: appDir, stdio: "inherit" },
);

if (result.error) {
  throw result.error;
}
process.exit(result.status ?? 1);
