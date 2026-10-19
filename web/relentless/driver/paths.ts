import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const relentlessDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const appDir = resolve(relentlessDir, '..')
const repoRoot = resolve(appDir, '..', '..', '..')

export const paths = {
  relentlessDir,
  appDir,
  repoRoot,
  rdstDir: resolve(repoRoot, 'rdst'),
  distDir: resolve(appDir, 'dist'),
  cli: resolve(relentlessDir, 'cli.ts'),
  daemon: resolve(relentlessDir, 'driver', 'daemon.ts'),
  // CI's RELENTLESS_UI_RUNS_DIR moves every run, not just its own files.
  runsDir: resolve(
    process.env.RELENTLESS_UI_RUNS_DIR ??
      resolve(appDir, 'test-results', 'relentless')
  ),
}
