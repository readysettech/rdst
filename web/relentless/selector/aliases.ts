import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Paths moved since the contract was written (`aliases.json`, old path to
 * new). An older tree still has the old paths, so the selector reads every
 * path through `canonical` before matching it against entries, manifests,
 * and flows. A moved directory maps everything under it.
 */

const here = dirname(fileURLToPath(import.meta.url))
const file = join(here, 'aliases.json')
const moves: [string, string][] = existsSync(file)
  ? Object.entries(
      (
        JSON.parse(readFileSync(file, 'utf8')) as {
          moves: Record<string, string>
        }
      ).moves
    ).sort((a, b) => b[0].length - a[0].length)
  : []
const exact = new Map(moves)

export function canonical(path: string) {
  const hit = exact.get(path)
  if (hit) return hit
  for (const [from, to] of moves) {
    if (path.startsWith(`${from}/`)) return to + path.slice(from.length)
  }
  return path
}

/** Problems with the table, for `--check`: every target exists, no source does. */
export function checkAliases(trackedFiles: string[]) {
  const tracked = new Set(trackedFiles)
  const under = (dir: string) =>
    trackedFiles.some((f) => f.startsWith(`${dir}/`))
  const problems: string[] = []
  for (const [from, to] of moves) {
    if (!tracked.has(to) && !under(to))
      problems.push(`alias target ${to} does not exist`)
    if (tracked.has(from) || under(from))
      problems.push(`alias source ${from} still exists`)
  }
  return problems
}
