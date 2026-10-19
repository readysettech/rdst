import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Selection, Vcs } from './selector.ts'

/**
 * A model's reading of a change: the areas and flows it would run, from the
 * change's description, files, and diff, and the tester's own area and flow
 * descriptions. `evaluation/llm-select.ts` measures it on past changes; the
 * selector itself asks no model. The diff is untrusted input, so a model's
 * answer could only ever add areas, never remove one.
 */

const here = dirname(fileURLToPath(import.meta.url))
const relentless = join(here, '..')
export const DEFAULT_MODEL = 'z-ai/glm-5.3-flash'
/** Extra areas a model may suggest for one change. */
export const MAX_MODEL_AREAS = 2

export interface ModelAnswer {
  areas: string[]
  flows: string[]
  reason: string
}

export interface ModelUsage {
  prompt_tokens: number
  completion_tokens: number
  cost?: number
  completion_tokens_details?: { reasoning_tokens?: number }
}

// What each area and flow tests: the first paragraph of its own description.
const firstParagraph = (text: string) =>
  text
    .split('\n\n')
    .find((p) => p.trim() && !p.startsWith('#') && !/^\w+: /.test(p))
    ?.replace(/\n/g, ' ')
    .trim() ?? ''

export const areaNames = readdirSync(join(relentless, 'areas'))
  .filter((f) => f.endsWith('.md'))
  .map((f) => basename(f, '.md'))
  .sort()

export const flowCatalog = readdirSync(join(relentless, 'flows'))
  .flatMap((capability) =>
    readdirSync(join(relentless, 'flows', capability)).map((file) => {
      const text = readFileSync(
        join(relentless, 'flows', capability, file),
        'utf8'
      )
      return {
        id: `${capability}/${basename(file, '.md')}`,
        minutes: Number(text.match(/^Minutes: (\d+)/m)?.[1] ?? 10),
        text: firstParagraph(text),
      }
    })
  )
  .sort((a, b) => a.id.localeCompare(b.id))

const areaGuide = areaNames
  .map(
    (a) =>
      `- ${a}: ${firstParagraph(readFileSync(join(relentless, 'areas', `${a}.md`), 'utf8'))}`
  )
  .join('\n')
const flowGuide = flowCatalog.map((f) => `- ${f.id}: ${f.text}`).join('\n')

export const systemPrompt = `You choose which UI test areas and flows to run for a code change to RDST, a database performance assistant with a React web app (web-apps/apps/rdst), an Electron desktop shell (web-apps/apps/rdst-desktop), and a Python FastAPI backend (rdst/). An AI tester runs against the real app one area at a time; each area costs 10-30 minutes, so choose the MINIMUM set of areas a careful reviewer would insist on to exercise the user-visible behavior the change can alter.

Areas:
${areaGuide}

Scope rules:
- Only UI reachable from the app's navigation counts. The /scan and /guards pages are out of scope.
- CLI-only changes (rdst/shared/cli, */cli/*, rdst.py, mcp_server.py) need no area unless the same code also serves the web API.
- rdst/keyservice/** is a separate Cloudflare Worker; the tester runs against the staging keyservice, so keyservice-only changes need no area.
- Tests, docs, aidoc, CI configuration (.buildkite), and dev tooling (rdst/devtools) need no area.
- "ai-access" is the AI gate, the Anthropic key dialog, the provider choice, and Readyset account sign-in. "settings-targets" includes the Settings page's AI panel and data & privacy. "shell-navigation" is the sidebar, header, feedback dialog, error pages, keyboard/offline behavior. "app-lifecycle" is reload/restart/backend-crash behavior. "desktop-shell" is the Electron window, menu, zoom, packaging.
- Backend changes matter through what the UI does with them.

Flows are short targeted investigations, separate from areas; list those whose behavior the change can plausibly break, usually none:
${flowGuide}

Reply with only a JSON object: {"areas": ["area", ...], "flows": ["flow-id", ...], "reason": "one sentence"}. Use the exact names above; an empty list means none is needed.`

export const SKIP_DIFF =
  /(\.lock|lock\.yaml|\.svg|\.png|\.snap|openapi\.schema\.json)$/
const FILE_BUDGET = 8_000
const DIFF_BUDGET = 60_000

/** The change as the model reads it: description, files, and a bounded diff. */
export function packetFor(vcs: Vcs, root: string) {
  const run = (cmd: string, args: string[]) =>
    execFileSync(cmd, args, {
      cwd: root,
      encoding: 'utf8',
      maxBuffer: 256 << 20,
    })
  const description =
    vcs.kind === 'jj'
      ? run('jj', ['log', '-r', vcs.rev, '--no-graph', '-T', 'description'])
      : run('git', ['log', '-1', '--format=%B', vcs.head])
  const files = (
    vcs.kind === 'jj'
      ? run('jj', ['diff', '-r', vcs.rev, '--summary'])
      : run('git', ['diff', '--name-status', '-M', vcs.base, vcs.head])
  )
    .trim()
    .split('\n')
    .filter(Boolean)
  // Paths come from a delimiter-safe listing, so names with spaces survive.
  const paths = (
    vcs.kind === 'jj'
      ? run('jj', ['diff', '-r', vcs.rev, '--name-only']).split('\n')
      : run('git', [
          'diff',
          '--name-only',
          '-z',
          '-M',
          vcs.base,
          vcs.head,
        ]).split('\0')
  ).filter(Boolean)
  let diff = ''
  for (const path of paths) {
    if (SKIP_DIFF.test(path) || diff.length > DIFF_BUDGET) continue
    const part =
      vcs.kind === 'jj'
        ? run('jj', [
            'diff',
            '-r',
            vcs.rev,
            '--git',
            '--context',
            '2',
            `file:${JSON.stringify(path)}`,
          ])
        : run('git', ['diff', '-U2', '-M', vcs.base, vcs.head, '--', path])
    diff +=
      part.length > FILE_BUDGET
        ? `${part.slice(0, FILE_BUDGET)}\n[... truncated]\n`
        : part
  }
  if (diff.length > DIFF_BUDGET)
    diff = `${diff.slice(0, DIFF_BUDGET)}\n[... diff truncated]\n`
  return `Change description:\n${description}\n\nFiles (${files.length}):\n${files.slice(0, 300).join('\n')}\n\nDiff:\n${diff}`
}

const strings = (value: unknown) =>
  Array.isArray(value)
    ? value.filter((v): v is string => typeof v === 'string')
    : []

/** Asks the model through OpenRouter; retries transient failures. */
export async function askModel(
  content: string,
  options: {
    key: string
    model?: string
    /** An effort (`low`, `medium`, `high`) or a reasoning token budget. */
    reasoning?: string
    timeoutMs?: number
    attempts?: number
  }
) {
  // A key with whitespace or control characters would be echoed back in the
  // fetch error; refuse it without repeating it.
  if (!/^[\x21-\x7e]+$/.test(options.key))
    throw new Error(
      'the OpenRouter key contains whitespace or control characters'
    )
  const reasoning = options.reasoning
    ? /^\d+$/.test(options.reasoning)
      ? { max_tokens: Number(options.reasoning) }
      : { effort: options.reasoning }
    : undefined
  const attempts = options.attempts ?? 4
  for (let attempt = 1; ; attempt++) {
    try {
      const response = await fetch(
        'https://openrouter.ai/api/v1/chat/completions',
        {
          method: 'POST',
          headers: {
            authorization: `Bearer ${options.key}`,
            'content-type': 'application/json',
          },
          signal: AbortSignal.timeout(options.timeoutMs ?? 900_000),
          body: JSON.stringify({
            model: options.model ?? DEFAULT_MODEL,
            temperature: 0,
            ...(reasoning ? { reasoning } : {}),
            response_format: { type: 'json_object' },
            messages: [
              { role: 'system', content: systemPrompt },
              { role: 'user', content },
            ],
          }),
        }
      )
      const body = (await response.json()) as {
        choices?: { message: { content: string } }[]
        usage?: ModelUsage
        error?: { message: string }
      }
      const text = body.choices?.[0]?.message.content
      if (!response.ok || !text)
        throw new Error(
          `${response.status} ${body.error?.message ?? text ?? ''}`
        )
      const raw = JSON.parse(
        text.replace(/^```(json)?\s*|\s*```$/g, '')
      ) as Record<string, unknown>
      const answer: ModelAnswer = {
        areas: strings(raw.areas),
        flows: strings(raw.flows),
        reason: typeof raw.reason === 'string' ? raw.reason : '',
      }
      return { answer, usage: body.usage }
    } catch (error) {
      if (attempt >= attempts)
        throw new Error(String(error).replaceAll(options.key, '[redacted]'))
      await new Promise((r) => setTimeout(r, 2_000 * attempt))
    }
  }
}

/**
 * What a model's answer adds to a selection: known areas it did not
 * recommend, at most `MAX_MODEL_AREAS`, and known flows it did not plan.
 */
export function extraFromModel(answer: ModelAnswer, selection: Selection) {
  const recommended = new Set(selection.recommended_areas)
  const planned = new Set(
    [...selection.flows, ...selection.deferred_flows].map((f) => f.id)
  )
  const flowIds = new Set(flowCatalog.map((f) => f.id))
  return {
    areas: answer.areas
      .filter((a) => areaNames.includes(a) && !recommended.has(a))
      .slice(0, MAX_MODEL_AREAS),
    flows: answer.flows.filter((f) => flowIds.has(f) && !planned.has(f)),
    invalid: [
      ...answer.areas.filter((a) => !areaNames.includes(a)),
      ...answer.flows.filter((f) => !flowIds.has(f)),
    ],
  }
}
