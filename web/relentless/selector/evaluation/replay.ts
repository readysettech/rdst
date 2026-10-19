import { execFile, execFileSync } from 'node:child_process'
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { selectorFingerprint } from './fingerprint.ts'

/**
 * Replays the selector on labeled past changes, each on its own tree in a
 * scratch jj workspace, and appends one JSON line per change to
 * `OUT/replay.jsonl`. A change already in the file is skipped, so an
 * interrupted replay resumes; `OUT/replay.meta.json` fingerprints the selector,
 * and a replay written by another refuses to resume. `report.ts` turns the
 * result into tables.
 *
 *   node replay.ts --out DIR [--workers 4] [--source reviewed|agent] [--since 2026-02-19]
 *     [--split tune|held-out]
 *
 * Changes with findings evidence (`finding-labels.json`) are replayed too.
 *
 * The selector code and contract come from this checkout, so a replay
 * measures today's selector against yesterday's code.
 */

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '../../../../../..')
const selectTs = join(here, '..', 'select.ts')
const { values } = parseArgs({
  options: {
    out: { type: 'string' },
    workers: { type: 'string', default: '4' },
    source: { type: 'string' },
    since: { type: 'string' },
    split: { type: 'string' },
  },
})
if (!values.out) throw new Error('--out DIR is required')
const out = values.out
mkdirSync(out, { recursive: true })
const replayPath = join(out, 'replay.jsonl')

const meta = { selector: selectorFingerprint() }
const metaPath = join(out, 'replay.meta.json')
if (existsSync(replayPath)) {
  const previous = existsSync(metaPath)
    ? (JSON.parse(readFileSync(metaPath, 'utf8')) as typeof meta)
    : null
  if (previous?.selector !== meta.selector) {
    throw new Error(
      `${out} was replayed with another selector; use a new --out`
    )
  }
}
writeFileSync(metaPath, `${JSON.stringify(meta, null, 2)}\n`)

interface Label {
  commit: string
  date: string
  title: string
  source: string
  split?: string
}
const labeled = (
  JSON.parse(readFileSync(join(here, 'labels.json'), 'utf8')) as {
    changes: Label[]
  }
).changes
const findingPath = join(here, 'finding-labels.json')
const known = new Set(labeled.map((l) => l.commit))
const evidenced: Label[] = existsSync(findingPath)
  ? (
      JSON.parse(readFileSync(findingPath, 'utf8')) as {
        changes: { commit: string }[]
      }
    ).changes
      .filter((c) => !known.has(c.commit))
      .map((c) => ({
        commit: c.commit,
        date: execFileSync(
          'jj',
          [
            'log',
            '--no-graph',
            '-r',
            c.commit,
            '-T',
            'committer.timestamp().format("%Y-%m-%d")',
          ],
          { cwd: repoRoot, encoding: 'utf8' }
        ).trim(),
        title: '',
        source: 'finding',
        split: 'finding',
      }))
  : []
const labels = [...labeled, ...evidenced].filter(
  (l) =>
    (!values.source || l.source === values.source) &&
    (!values.since || l.date >= values.since) &&
    (!values.split || l.split === values.split || l.split === 'finding')
)
const contract = JSON.parse(
  readFileSync(join(here, '..', 'areas.json'), 'utf8')
) as { areas: Record<string, { entries: string[] }> }
const done = new Set(
  existsSync(replayPath)
    ? readFileSync(replayPath, 'utf8')
        .split('\n')
        .filter(Boolean)
        .map((line) => (JSON.parse(line) as { commit: string }).commit)
    : []
)
const queue = labels.filter((l) => !done.has(l.commit))

const jj = (cwd: string, args: string[]) =>
  execFileSync('jj', args, {
    cwd,
    encoding: 'utf8',
    maxBuffer: 256 << 20,
    stdio: ['ignore', 'pipe', 'pipe'],
  })

function selectIn(workspace: string, commit: string): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(
      process.execPath,
      [selectTs, '--jj-rev', commit],
      {
        cwd: workspace,
        encoding: 'utf8',
        maxBuffer: 64 << 20,
        env: {
          ...process.env,
          RT_SELECTOR_ROOT: workspace,
          RT_SELECTOR_HISTORY: join(here, 'history.json'),
        },
      },
      (error, stdout, stderr) =>
        error ? reject(new Error(stderr || error.message)) : resolve(stdout)
    )
  })
}

// Each scratch workspace is a full checkout; one left behind fills a tmpfs.
const workspaces = new Map<string, string>()
const removeWorkspace = (name: string) => {
  try {
    jj(repoRoot, ['workspace', 'forget', name])
  } catch {}
  rmSync(workspaces.get(name) ?? '', { recursive: true, force: true })
  workspaces.delete(name)
}
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => {
    for (const name of [...workspaces.keys()]) removeWorkspace(name)
    process.exit(1)
  })
}

async function worker(index: number) {
  const workspace = mkdtempSync(join(tmpdir(), `rt-replay-${index}-`))
  const name = `rt-replay-${process.pid}-${index}`
  workspaces.set(name, workspace)
  try {
    jj(repoRoot, ['workspace', 'add', '--name', name, '-r', '@', workspace])
    for (let next = queue.shift(); next; next = queue.shift()) {
      const started = Date.now()
      let record: Record<string, unknown> = {
        commit: next.commit,
        date: next.date,
      }
      try {
        jj(workspace, ['new', next.commit])
        const selection = JSON.parse(
          await selectIn(workspace, next.commit)
        ) as Record<string, unknown>
        const entriesPresent = Object.fromEntries(
          Object.entries(contract.areas).map(([area, spec]) => [
            area,
            spec.entries.some((entry) => existsSync(join(workspace, entry))),
          ])
        )
        record = {
          ...record,
          ...selection,
          entries_present: entriesPresent,
          seconds: Math.round((Date.now() - started) / 1000),
        }
        delete record.ignored
      } catch (error) {
        record = { ...record, error: String(error).slice(0, 500) }
      }
      appendFileSync(replayPath, `${JSON.stringify(record)}\n`)
      process.stderr.write(record.error ? 'E' : '.')
    }
  } finally {
    removeWorkspace(name)
  }
}

process.stderr.write(`${queue.length} changes to replay\n`)
// Every worker finishes and removes its workspace before a failure surfaces.
const outcomes = await Promise.allSettled(
  Array.from({ length: Number(values.workers) }, (_, i) => worker(i))
)
for (const outcome of outcomes)
  if (outcome.status === 'rejected') throw outcome.reason
process.stderr.write('\n')
