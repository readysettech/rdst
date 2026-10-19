import { createHash } from 'node:crypto'
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import {
  areaNames,
  askModel,
  DEFAULT_MODEL,
  flowCatalog,
  packetFor,
  systemPrompt,
} from '../model.ts'

/**
 * Asks a model to choose the areas and flows for each labeled past change,
 * from its description, files, and diff, and writes the answers as a replay
 * (`OUT/replay.jsonl`) that `report.ts` grades, with the prompt in
 * `model.ts`: the tester's area and flow descriptions and the labelers'
 * scope rules, never the labels or the selector's output. A change already
 * in the file is skipped; `OUT/llm.meta.json` records the model, reasoning,
 * and prompt, and a run under others refuses to resume.
 *
 *   node llm-select.ts --out DIR [--model z-ai/glm-5.3-flash] [--workers 8]
 *     [--reasoning low|medium|high|TOKENS] [--commits FILE] [--entries REPLAY_DIR]
 *
 * The OpenRouter key comes from RDST_RELENTLESS_OPENROUTER_API_KEY_FILE.
 * `--commits` limits the run to a JSON array of commits; `--entries` copies
 * each change's `entries_present` from another replay, so the report can
 * split it by era. `--reasoning` caps the model's thinking by effort or by
 * a token budget; unset, the model decides.
 */

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '../../../../../..')
const { values } = parseArgs({
  options: {
    out: { type: 'string' },
    model: { type: 'string', default: DEFAULT_MODEL },
    workers: { type: 'string', default: '8' },
    commits: { type: 'string' },
    entries: { type: 'string' },
    reasoning: { type: 'string' },
  },
})
if (!values.out) throw new Error('--out DIR is required')
const out = values.out
mkdirSync(out, { recursive: true })
const replayPath = join(out, 'replay.jsonl')
const meta = `${JSON.stringify(
  {
    model: values.model,
    reasoning: values.reasoning ?? null,
    prompt: createHash('sha256').update(systemPrompt).digest('hex'),
  },
  null,
  2
)}\n`
const metaPath = join(out, 'llm.meta.json')
if (
  existsSync(replayPath) &&
  (!existsSync(metaPath) || readFileSync(metaPath, 'utf8') !== meta)
) {
  throw new Error(
    `${out} was written with another model, reasoning, or prompt; use a new --out`
  )
}
writeFileSync(metaPath, meta)

const keyFile = process.env.RDST_RELENTLESS_OPENROUTER_API_KEY_FILE
if (!keyFile) throw new Error('set RDST_RELENTLESS_OPENROUTER_API_KEY_FILE')
const key = readFileSync(keyFile, 'utf8').trim()

interface Label {
  commit: string
  date: string
}
const only = values.commits
  ? new Set(JSON.parse(readFileSync(values.commits, 'utf8')) as string[])
  : null
const done = new Set(
  existsSync(replayPath)
    ? readFileSync(replayPath, 'utf8')
        .split('\n')
        .filter(Boolean)
        .map((line) => (JSON.parse(line) as { commit: string }).commit)
    : []
)
const queue = (
  JSON.parse(readFileSync(join(here, 'labels.json'), 'utf8')) as {
    changes: Label[]
  }
).changes.filter((l) => (!only || only.has(l.commit)) && !done.has(l.commit))
const entries = new Map<string, unknown>()
if (values.entries) {
  for (const line of readFileSync(
    join(values.entries, 'replay.jsonl'),
    'utf8'
  ).split('\n')) {
    if (!line) continue
    const r = JSON.parse(line) as { commit: string; entries_present?: unknown }
    entries.set(r.commit, r.entries_present)
  }
}

async function worker() {
  for (let next = queue.shift(); next; next = queue.shift()) {
    const started = Date.now()
    let record: Record<string, unknown> = {
      commit: next.commit,
      date: next.date,
    }
    try {
      const { answer, usage } = await askModel(
        packetFor({ kind: 'jj', rev: next.commit }, repoRoot),
        { key, model: values.model, reasoning: values.reasoning }
      )
      const areas = answer.areas.filter((a) => areaNames.includes(a))
      const flows = flowCatalog.filter((f) => answer.flows.includes(f.id))
      record = {
        ...record,
        status: areas.length > 0 || flows.length > 0 ? 'targeted' : 'none',
        recommended_areas: areas,
        affected_areas: areas,
        flows: flows.map((f) => ({ id: f.id, minutes: f.minutes })),
        mission_minutes: flows.reduce((n, f) => n + f.minutes, 0),
        entries_present: entries.get(next.commit),
        invalid: [
          ...answer.areas.filter((a) => !areaNames.includes(a)),
          ...answer.flows.filter((f) => !flowCatalog.some((x) => x.id === f)),
        ],
        reason: answer.reason,
        usage,
        seconds: Math.round((Date.now() - started) / 1000),
      }
    } catch (error) {
      record = {
        ...record,
        error: String(error).replaceAll(key, '[redacted]').slice(0, 500),
      }
    }
    appendFileSync(replayPath, `${JSON.stringify(record)}\n`)
    process.stderr.write(record.error ? 'E' : '.')
  }
}

process.stderr.write(`${queue.length} changes to select with ${values.model}\n`)
await Promise.all(
  Array.from({ length: Number(values.workers) }, () => worker())
)
process.stderr.write('\n')
