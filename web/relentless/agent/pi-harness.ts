import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'
import { readableFrom } from './read-guard.ts'

/**
 * The tester agent's tools under pi: `rt`, run without a shell, and pi's
 * own read, limited to the run directory outside the app's HOME, which holds
 * what the app was issued. pi starts with every other tool disabled, so this
 * is all the agent can do, as under Claude Code's `Bash(rt:*)` and
 * `Read(runDir/**)`. When RDST_RELENTLESS_MAX_TURNS or
 * RDST_RELENTLESS_BUDGET_USD is set, it also stops the agent after that many
 * turns, or once the models' reported cost reaches it, recording why in
 * RDST_RELENTLESS_PI_STOP_FILE. A throttled request is retried like any
 * other transient provider error.
 */

const runDir = resolve(process.env.RDST_RELENTLESS_RUN_DIR ?? '.')
const readable = readableFrom(runDir)
const maxTurns =
  Number(process.env.RDST_RELENTLESS_MAX_TURNS) || Number.POSITIVE_INFINITY
const budgetUsd =
  Number(process.env.RDST_RELENTLESS_BUDGET_USD) || Number.POSITIVE_INFINITY
const stopFile = process.env.RDST_RELENTLESS_PI_STOP_FILE
// A snapshot of a busy page runs to tens of kilobytes; more only crowds out
// the context.
const OUTPUT_LIMIT = 60_000

function rt(args: string[], signal?: AbortSignal) {
  return new Promise<{ code: number | null; output: string }>((done) => {
    // rt needs nothing of pi's, least of all the proxy key.
    const env = Object.fromEntries(
      Object.entries(process.env).filter(
        ([name]) => !name.startsWith('CLIPROXYAPI_')
      )
    )
    const child = spawn('rt', args, { cwd: runDir, env, signal })
    let output = ''
    child.stdout.on('data', (chunk) => {
      output += chunk
    })
    child.stderr.on('data', (chunk) => {
      output += chunk
    })
    child.on('error', (error) => done({ code: null, output: String(error) }))
    child.on('close', (code) => done({ code, output }))
  })
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: 'rt',
    label: 'rt',
    description:
      'Drive the application under test. Pass the words you would type after `rt`, one per element: ["snapshot"], ["click", "e12"], ["fill", "e4", "hello world"], ["api", "GET", "/api/status"].',
    parameters: Type.Object({
      args: Type.Array(Type.String(), {
        description: 'The rt subcommand and its arguments',
      }),
    }),
    async execute(_id, params, signal) {
      const { code, output } = await rt(params.args, signal)
      const text =
        output.length > OUTPUT_LIMIT
          ? `${output.slice(0, OUTPUT_LIMIT)}\n[... ${output.length - OUTPUT_LIMIT} more characters]`
          : output
      if (code !== 0) throw new Error(text || `rt exited with ${code}`)
      return { content: [{ type: 'text', text }], details: {} }
    },
  })

  pi.on('tool_call', async (event) => {
    if (event.toolName !== 'read') return
    const input = event.input as { path?: string; file_path?: string }
    const path = input.path ?? input.file_path ?? ''
    if (!readable(path)) {
      return {
        block: true,
        reason: `Only existing files inside the run directory (${runDir}), outside its home/, can be read.`,
      }
    }
  })

  // Codex reports throttling of a burst of requests as a usage limit with no
  // reset time, and the proxy answers auth_unavailable while its login cools
  // down after a failed upstream request; pi retries neither. A real usage
  // limit says when it ends.
  pi.on('message_end', async ({ message }) => {
    if (message.role !== 'assistant' || message.stopReason !== 'error') return
    const error = message.errorMessage ?? ''
    const throttled =
      error.startsWith('auth_unavailable') ||
      (error.includes('usage limit') && !error.includes('Try again'))
    if (!throttled) return
    return { message: { ...message, errorMessage: `rate limit: ${error}` } }
  })

  let turns = 0
  let costUsd = 0
  pi.on('turn_end', async (event, ctx) => {
    turns += 1
    const usage = (event.message as { usage?: { cost?: { total?: number } } })
      .usage
    costUsd += usage?.cost?.total ?? 0
    const reason =
      turns >= maxTurns ? 'max_turns' : costUsd >= budgetUsd ? 'budget' : null
    if (!reason) return
    if (stopFile)
      writeFileSync(stopFile, JSON.stringify({ reason, turns, costUsd }))
    ctx.abort()
  })
}
