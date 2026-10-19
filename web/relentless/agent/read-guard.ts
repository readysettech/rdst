import { realpathSync } from 'node:fs'
import { isAbsolute, join, relative, resolve } from 'node:path'

const within = (dir: string, path: string) => {
  const rel = relative(dir, path)
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))
}

const canonical = (path: string) => {
  try {
    return realpathSync(path)
  } catch {
    return undefined
  }
}

/**
 * Which paths the tester agent may read under pi: existing files inside the
 * run directory, outside the app's HOME, which holds what the app was issued,
 * and other than the raw logs, which `rt logs` reads redacted. pi's read
 * turns Unicode spaces into plain ones, drops a leading @, and expands ~ and
 * file: URLs before it opens a path, so the guard does the first, refuses the
 * other forms, and checks the rest where their symlinks lead.
 */
export function readableFrom(runDir: string) {
  const root = canonical(runDir) ?? resolve(runDir)
  return (raw: string) => {
    const path = raw.replace(/[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g, ' ')
    if (/^(?:@|~|file:)/.test(path)) return false
    const target = canonical(resolve(runDir, path))
    return (
      target !== undefined &&
      within(root, target) &&
      !within(join(root, 'home'), target) &&
      !target.endsWith('.log')
    )
  }
}
