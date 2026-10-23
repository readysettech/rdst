#!/usr/bin/env node
// A stand-in for `claude -p` in a relentless run's home. RDST starts it the way it starts
// Claude Code, printing stream-json as it goes and calling RDST's MCP server named in
// --mcp-config.
// - A search reads the projects named by ~/.claude/projects sessions, finds database settings
//   in their .env files, and proposes each one as it finds it. It never sends a password.
// - A setup (the prompt lists "Save as NAME: ..." lines) adds each database, checks its
//   prerequisites and turns monitoring on, waiting while RDST waits on the user's click.
const { existsSync, readdirSync, readFileSync, statSync } = require('fs')
const http = require('http')
const { homedir } = require('os')
const { join } = require('path')

const STEP_MS = 1200
const args = process.argv.slice(2)
const prompt = args[args.indexOf('-p') + 1] || ''
const configPath = args[args.indexOf('--mcp-config') + 1]
const session = args.includes('--resume')
  ? args[args.indexOf('--resume') + 1]
  : '00000000-0000-4000-8000-000000000001'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const emit = (event) => process.stdout.write(`${JSON.stringify(event)}\n`)
const say = async (text) => {
  emit({ type: 'assistant', message: { content: [{ type: 'text', text }] } })
  await sleep(STEP_MS / 2)
}
const tool = async (name, input) => {
  emit({ type: 'assistant', message: { content: [{ type: 'tool_use', name, input }] } })
  await sleep(STEP_MS)
}

function post(url, body) {
  return new Promise((resolve, reject) => {
    const request = http.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    }, (response) => {
      let reply = ''
      response.on('data', (chunk) => { reply += chunk })
      response.on('end', () => resolve({ status: response.statusCode, reply }))
    })
    request.on('error', reject)
    request.end(JSON.stringify(body))
  })
}

/** Calls an RDST tool and returns its parsed reply; exits when RDST refuses it. */
async function rdst(name, input) {
  await tool(`mcp__rdst__${name}`, input)
  const [{ url }] = Object.values(JSON.parse(readFileSync(configPath, 'utf8')).mcpServers)
  const { status, reply } = await post(url, {
    jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: input },
  })
  const data = reply.includes('data: ')
    ? reply.split('\n').filter((line) => line.startsWith('data: ')).pop().slice(6)
    : reply
  const result = status === 200 ? JSON.parse(data).result : null
  if (!result || result.isError) {
    process.stderr.write(`RDST refused ${name}: ${reply}\n`)
    process.exit(1)
  }
  return JSON.parse(result.content[0].text)
}

/** The projects recent sessions ran in, the most recently used first. */
function projects() {
  const root = join(homedir(), '.claude', 'projects')
  if (!existsSync(root)) return []
  const sessions = []
  for (const dir of readdirSync(root)) {
    for (const file of readdirSync(join(root, dir)).filter((f) => f.endsWith('.jsonl'))) {
      sessions.push(join(root, dir, file))
    }
  }
  sessions.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
  const found = new Set()
  for (const session of sessions) {
    for (const line of readFileSync(session, 'utf8').split('\n')) {
      try {
        const cwd = JSON.parse(line).cwd
        if (cwd && existsSync(cwd)) found.add(cwd)
      } catch {}
    }
  }
  return [...found]
}

function parseEnv(text) {
  const vars = {}
  for (const line of text.split('\n')) {
    const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*"?([^"\n]*)"?\s*$/)
    if (match) vars[match[1]] = match[2]
  }
  return vars
}

function providerOf(host) {
  if (/supabase\.(co|com)$/.test(host)) return 'supabase'
  if (/neon\.tech$/.test(host)) return 'neon'
  if (/rds\.amazonaws\.com$/.test(host)) return 'aws'
  if (/^(localhost|127\.0\.0\.1|postgres|mysql)$/.test(host)) return 'local'
  return 'other'
}

/** The databases one project's .env files name, as rdst_propose_databases takes them. */
function databasesIn(project) {
  const found = []
  for (const file of readdirSync(project).filter((f) => f.startsWith('.env'))) {
    const path = join(project, file)
    const vars = parseEnv(readFileSync(path, 'utf8'))
    const base = { name: project.split('/').pop(), project, source: path }
    for (const [name, value] of Object.entries(vars)) {
      if (!/^(postgres(ql)?|mysql):\/\//.test(value)) continue
      const url = new URL(value)
      found.push({
        ...base, engine: url.protocol.startsWith('mysql') ? 'mysql' : 'postgresql',
        host: url.hostname, port: Number(url.port) || null,
        database: url.pathname.slice(1) || null, user: decodeURIComponent(url.username) || null,
        provider: providerOf(url.hostname),
        environment: /prod/i.test(name) ? 'production' : 'development',
        ...(url.password ? { env_file: path, env_var: name } : {}),
      })
    }
    if (vars.DB_HOST) {
      found.push({
        ...base, engine: /mysql/i.test(vars.DB_ENGINE || '') ? 'mysql' : 'postgresql',
        host: vars.DB_HOST, port: Number(vars.DB_PORT) || null, database: vars.DB_NAME || null,
        user: vars.DB_USER || null, provider: providerOf(vars.DB_HOST),
        environment: 'development',
        ...(vars.DB_PASSWORD ? { password_file: path, password_var: 'DB_PASSWORD' } : {}),
      })
    }
  }
  return found
}

async function search() {
  await say('Looking through your recent Claude Code sessions for the projects you work in.')
  await tool('Glob', { pattern: '**/*.jsonl', path: join(homedir(), '.claude', 'projects') })
  let count = 0
  for (const project of projects()) {
    await say(`Checking ${project.split('/').pop()} for database settings.`)
    await tool('Read', { file_path: join(project, '.env') })
    const found = databasesIn(project)
    if (found.length === 0) continue
    count += found.length
    await rdst('rdst_propose_databases', { databases: found })
  }
  emit({ type: 'result', session_id: session, result: `Found ${count} databases.` })
}

async function setup() {
  const picks = [...prompt.matchAll(/^- Save as ([^:]+): (.*)$/gm)]
  for (const [, name, details] of picks) {
    const project = (details.match(/project ([^,]+)/) || [])[1]
    const where = project && project.replace(/^~/, homedir())
    const found = where && existsSync(where)
      ? databasesIn(where).find((item) => item.env_file || item.password_file)
      : null
    if (!found) {
      await say(`I couldn't find ${name}'s password in its project, so it needs your details.`)
      continue
    }
    await say(`Adding ${name} to RDST with the settings from its project.`)
    const fields = found.env_file
      ? { env_file: found.env_file, env_var: found.env_var }
      : {
          engine: found.engine, host: found.host, port: found.port, database: found.database,
          user: found.user,
          password: parseEnv(readFileSync(found.password_file, 'utf8'))[found.password_var],
        }
    const added = await rdst('rdst_add_database', { name, ...fields })
    await say(`Checking that RDST can read ${added.target}'s query statistics.`)
    const ready = await rdst('rdst_check_prerequisites', { name: added.target })
    if (ready.ready_to_monitor === false) {
      await say(`${added.target} needs setup RDST describes; I'll leave that to you.`)
      continue
    }
    const enabled = await rdst('rdst_enable_monitoring', { name: added.target })
    if (enabled.waiting_for_user) {
      await say("RDST is showing you its Turn on monitoring screen. I'll wait for your click.")
      for (let i = 0; i < 12; i++) {
        if ((await rdst('rdst_wait_for_monitoring', { name: added.target })).monitoring) break
      }
    }
  }
  emit({ type: 'result', session_id: session, result: `Set up ${picks.length} databases.` })
}

async function main() {
  emit({ type: 'system', subtype: 'init', session_id: session })
  await (/^- Save as /m.test(prompt) ? setup() : search())
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`)
  process.exit(1)
})
