#!/usr/bin/env node
/**
 * Runs the public-mirror overlay gates for this package.
 *
 * They live in the monorepo's `tools/public-mirror`, which the mirror does not
 * publish — only the assets under it are, at the paths they replace. So a
 * checkout of the public repo has this package and no gates to run, and
 * `turbo lint` there must succeed rather than fail on a missing path.
 */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const PACKAGE = dirname(dirname(fileURLToPath(import.meta.url)))
const GATES = join(PACKAGE, '..', '..', 'tools', 'public-mirror', 'scripts')

if (!existsSync(GATES)) {
  console.log('ui-icons: no public-mirror overlay in this checkout; skipping its gates')
  process.exit(0)
}

for (const gate of ['check-overlay-icons.mjs', 'check-overlay-brand.mjs']) {
  const { status } = spawnSync(process.execPath, [join(GATES, gate)], { stdio: 'inherit' })
  if (status !== 0) process.exit(status ?? 1)
}
