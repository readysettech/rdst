import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { capabilities } from '../ownership.ts'

const here = dirname(fileURLToPath(import.meta.url))
const selectorDir = join(here, '..')
const repoRoot = join(here, '../../../../../..')

/** Selector files that measure or test it but never decide a selection. */
const NOT_DECIDING = new Set([
  'evaluation',
  '__pycache__',
  'selector-tests.ts',
  'metrics.ts',
  'ingest-evidence.ts',
])

/**
 * A digest of everything that decides a selection: the selector, its
 * contract and evidence, the flows, and the ownership manifests. A replay is
 * only valid for the fingerprint it ran under.
 */
export function selectorFingerprint(): string {
  const hash = createHash('sha256')
  const hashDir = (dir: string) => {
    for (const entry of readdirSync(dir).sort()) {
      const path = join(dir, entry)
      if (NOT_DECIDING.has(entry)) continue
      if (statSync(path).isDirectory()) hashDir(path)
      else hash.update(relative(selectorDir, path)).update(readFileSync(path))
    }
  }
  hashDir(selectorDir)
  hashDir(join(selectorDir, '..', 'flows'))
  for (const { manifests } of capabilities().capabilities)
    for (const manifest of manifests)
      hash.update(manifest).update(readFileSync(join(repoRoot, manifest)))
  return hash.digest('hex')
}
