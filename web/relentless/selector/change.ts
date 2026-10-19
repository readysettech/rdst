import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { SKIP_DIFF } from './model.ts'

/**
 * A change as a change mission reads it: every commit's description from a
 * stack's base to its tip, the files, and the diff. `BASE..HEAD` is a whole
 * stack; a single revision is the range from its parent. Git is used where
 * the checkout has it, jj otherwise.
 *
 *   node change.ts --range BASE..HEAD --out DIR   (writes change.md, change.diff)
 *   node change.ts --stack-position HEAD_SHA      (Gerrit related changes on stdin)
 */

export interface ChangePacket {
  range: string
  commits: { id: string; description: string }[]
  /** One line per file: status and path, as the VCS prints them. */
  files: string[]
  diff: string
}

const MARK = '@@RT-COMMIT@@'
const DESCRIPTION_BUDGET = 20_000
const FILE_BUDGET = 8_000
const DIFF_BUDGET = 60_000

/** The diff without generated or binary files, which say nothing to a tester. */
export function filterDiff(diff: string) {
  return diff
    .split(/^(?=diff --git )/m)
    .filter((part) => {
      const path = part.match(/^diff --git a\/\S+ b\/(.+)$/m)?.[1] ?? ''
      return !SKIP_DIFF.test(path)
    })
    .join('')
}

export function changePacket(range: string, root: string): ChangePacket {
  const run = (cmd: string, args: string[]) =>
    execFileSync(cmd, args, {
      cwd: root,
      encoding: 'utf8',
      maxBuffer: 512 << 20,
    })
  const git = existsSync(join(root, '.git'))
  const [from, to] = range.includes('..')
    ? range.split('..')
    : [git ? `${range}~` : `${range}-`, range]
  const log = git
    ? run('git', [
        'log',
        '--reverse',
        `--format=${MARK}%n%H%n%B`,
        `${from}..${to}`,
      ])
    : run('jj', [
        'log',
        '-r',
        `${from}..${to}`,
        '--reversed',
        '--no-graph',
        '-T',
        `"${MARK}\\n" ++ commit_id ++ "\\n" ++ description`,
      ])
  const commits = log
    .split(`${MARK}\n`)
    .filter((part) => part.trim())
    .map((part) => {
      const [id, ...description] = part.split('\n')
      return { id: id.slice(0, 12), description: description.join('\n').trim() }
    })
  const files = (
    git
      ? run('git', ['diff', '--name-status', '-M', from, to])
      : run('jj', ['diff', '--from', from, '--to', to, '--summary'])
  )
    .split('\n')
    .filter(Boolean)
  const diff = filterDiff(
    git
      ? run('git', ['diff', '-U2', '-M', from, to])
      : run('jj', [
          'diff',
          '--from',
          from,
          '--to',
          to,
          '--git',
          '--context',
          '2',
        ])
  )
  return { range: `${from}..${to}`, commits, files, diff }
}

/**
 * The change for the mission prompt: every description, the files, and as
 * much of the diff as fits; the whole diff is in `change.diff` beside it.
 */
export function changeBrief(packet: ChangePacket) {
  let descriptions = packet.commits
    .map((c) => `### ${c.id}\n\n${c.description}`)
    .join('\n\n')
  if (descriptions.length > DESCRIPTION_BUDGET)
    descriptions = `${descriptions.slice(0, DESCRIPTION_BUDGET)}\n[... descriptions truncated]`
  let diff = packet.diff
  let truncated = false
  if (diff.length > DIFF_BUDGET) {
    truncated = true
    diff = ''
    for (const part of packet.diff.split(/^(?=diff --git )/m)) {
      if (diff.length > DIFF_BUDGET) break
      diff +=
        part.length > FILE_BUDGET
          ? `${part.slice(0, FILE_BUDGET)}\n[... file truncated]\n`
          : part
    }
  }
  const stack =
    packet.commits.length > 1
      ? `a stack of ${packet.commits.length} commits, tested together at its tip`
      : 'one commit'
  return `The change is ${stack} (${packet.range}). What each commit says it does:

${descriptions}

Files (${packet.files.length}):
${packet.files.slice(0, 400).join('\n')}

Diff${truncated ? ' (truncated here; the whole diff is `change.diff` in your run directory)' : ''}:
${diff}`
}

export function writeChange(packet: ChangePacket, dir: string) {
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'change.md'), changeBrief(packet))
  writeFileSync(join(dir, 'change.diff'), packet.diff)
}

interface Related {
  change_id?: string
  status?: string
  _change_number?: number
  _revision_number?: number
  _current_revision_number?: number
  commit: { commit: string; parents?: { commit: string }[] }
}

/**
 * Where a revision sits in its Gerrit stack, from the related-changes list:
 * the open changes above it at their current patchset, and the commit the
 * stack leaves from (the parent of its lowest open change), or null for a
 * change with no related changes.
 */
export function stackPosition(related: Related[], head: string) {
  const byCommit = new Map(related.map((r) => [r.commit.commit, r]))
  const parentOf = (r: Related) => r.commit.parents?.[0]?.commit
  const open = (r: Related | undefined) =>
    r?.status === 'NEW' && r._revision_number === r._current_revision_number
  const reachesHead = (r: Related) => {
    const seen = new Set<string>()
    for (let c: Related | undefined = r; c && !seen.has(c.commit.commit); ) {
      seen.add(c.commit.commit)
      if (parentOf(c) === head) return true
      c = byCommit.get(parentOf(c) ?? '')
    }
    return false
  }
  const above = related.filter(
    (r) => r.commit.commit !== head && open(r) && reachesHead(r)
  )
  let base: string | null = null
  const self = byCommit.get(head)
  for (let c = self; c; ) {
    const parent = byCommit.get(parentOf(c) ?? '')
    if (parent && open(parent)) c = parent
    else {
      base = parentOf(c) ?? null
      break
    }
  }
  return {
    tip: above.length === 0,
    above: above.map((r) => r._change_number ?? r.change_id),
    base,
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({
    options: {
      range: { type: 'string' },
      out: { type: 'string' },
      'stack-position': { type: 'string' },
    },
  })
  if (values['stack-position']) {
    // Gerrit prefixes JSON responses with )]}' against script inclusion.
    const text = readFileSync(0, 'utf8').replace(/^\)\]\}'\n?/, '')
    const related = (JSON.parse(text) as { changes?: Related[] }).changes ?? []
    process.stdout.write(
      `${JSON.stringify(stackPosition(related, values['stack-position']))}\n`
    )
  } else {
    if (!values.range || !values.out)
      throw new Error('usage: change.ts --range BASE..HEAD --out DIR')
    const root =
      process.env.RT_SELECTOR_ROOT ??
      join(import.meta.dirname, '../../../../..')
    writeChange(changePacket(values.range, root), values.out)
  }
}
