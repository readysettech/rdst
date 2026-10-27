import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

/**
 * Draft a Playwright spec from a relentless run: the driver commands that led
 * up to a finding, as Playwright calls, with the finding's expected and actual
 * behavior as the assertion to write. The draft is a starting point for a
 * deterministic regression spec under e2e/, not a finished test.
 */
const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { finding: { type: 'string' } },
})
const runDir = resolve(positionals[0] ?? '')
if (!existsSync(join(runDir, 'commands.jsonl'))) {
  console.error('usage: node relentless/to-spec.ts RUN_DIR [--finding F-001]')
  process.exit(2)
}

interface Logged {
  ts: string
  name: string
  event: 'start' | 'end'
  args?: string[]
  flags?: Record<string, string>
  ok?: boolean
  target?: string
  error?: string
}

const lines = readFileSync(join(runDir, 'commands.jsonl'), 'utf8')
  .split('\n')
  .filter(Boolean)
  .map((line) => JSON.parse(line) as Logged)

type Command = Logged & { result?: Logged }

// Commands run one at a time, so each start is followed by its end.
const commands: Command[] = []
for (const line of lines) {
  if (line.event === 'start') commands.push({ ...line })
  else if (commands.length) commands[commands.length - 1].result = line
}

const findings = existsSync(join(runDir, 'findings.jsonl'))
  ? readFileSync(join(runDir, 'findings.jsonl'), 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line) as Record<string, unknown>)
  : []
const finding = values.finding
  ? findings.find((entry) => entry.id === values.finding)
  : undefined
if (values.finding && !finding) {
  console.error(`no finding ${values.finding} in ${runDir}`)
  process.exit(2)
}
const until = finding ? String(finding.ts) : undefined

const quote = (text: string) => JSON.stringify(text)
const locator = (command: Command, index = 0) => {
  const target = command.result?.target ?? command.args?.[index] ?? ''
  return /^e\d+$/.test(target)
    ? `page.locator(${quote(`aria-ref=${target}`)}) /* TODO: ref was not resolved; use a role locator */`
    : `page.locator(${quote(target)})`
}

function translate(command: Command): string | null {
  const [a0 = '', a1 = ''] = command.args ?? []
  const flags = command.flags ?? {}
  switch (command.name) {
    case 'goto':
      return `await page.goto(${quote(a0)})`
    case 'reload':
    case 'back':
    case 'forward':
      return `await page.${command.name === 'back' ? 'goBack' : command.name === 'forward' ? 'goForward' : 'reload'}()`
    case 'click':
      return `await ${locator(command)}.click(${flags.double ? '{ clickCount: 2 }' : ''})`
    case 'hover':
      return `await ${locator(command)}.hover()`
    case 'fill':
      return `await ${locator(command)}.fill(${quote(a1)})`
    case 'fill-secret':
      return `await ${locator(command)}.fill(process.env.ANTHROPIC_API_KEY ?? '')`
    case 'type':
      return `await ${locator(command)}.pressSequentially(${quote(a1)})`
    case 'press':
      return flags.target
        ? `await page.locator(${quote(command.result?.target ?? flags.target)}).press(${quote(a0)})`
        : `await page.keyboard.press(${quote(a0)})`
    case 'select':
      return `await ${locator(command)}.selectOption(${quote(a1)})`
    case 'check':
    case 'uncheck':
      return `await ${locator(command)}.${command.name}()`
    case 'wait':
      if (flags.selector)
        return `await expect(page.locator(${quote(flags.selector)})).toBeVisible()`
      if (flags.text)
        return `await expect(page.getByText(${quote(flags.text)}).first()).toBeVisible()`
      if (flags.url)
        return `await page.waitForURL((url) => url.href.includes(${quote(flags.url)}))`
      if (flags.seconds)
        return `await page.waitForTimeout(${Number(flags.seconds) * 1000})`
      return null
    case 'scroll':
      return `await ${locator(command)}.scrollIntoViewIfNeeded()`
    case 'viewport':
      return `await page.setViewportSize({ width: ${Number(a0)}, height: ${Number(a1)} })`
    case 'eval':
      return `await page.evaluate(${quote(a0)})`
    case 'api':
      return `await page.request.fetch(${quote(a1)}, { method: ${quote(a0.toUpperCase())}${flags.json ? `, data: ${flags.json}` : ''} })`
    case 'restart':
    case 'kill-backend':
    case 'db':
    case 'mail':
    case 'menu':
    case 'tray':
    case 'tab':
      return `// TODO: rt ${command.name} ${(command.args ?? []).join(' ')} has no plain Playwright equivalent`
    default:
      return null
  }
}

const body: string[] = []
for (const command of commands) {
  if (until && command.ts > until) break
  if (command.name === 'finding' || command.name === 'finish') continue
  const line = translate(command)
  if (!line) continue
  if (command.result && command.result.ok === false) {
    body.push(
      `// failed during the run: ${String(command.result.error ?? '').split('\n')[0]}`
    )
    body.push(`// ${line}`)
  } else {
    body.push(line)
  }
}

const title = finding ? String(finding.title) : `replay of ${runDir}`
const header = finding
  ? [
      `// ${finding.id} ${finding.severity}: ${finding.title}`,
      `// Expected: ${finding.expected}`,
      `// Actual: ${finding.actual}`,
      `// Drafted from ${runDir}; review the steps and write the assertion.`,
    ]
  : [`// Drafted from ${runDir}.`]

console.log(`import { expect, test } from '../fixtures'

${header.join('\n')}
test(${quote(title)}, async ({ page }) => {
${body.map((line) => `  ${line}`).join('\n')}
  // TODO: assert the expected behavior above.
  expect(true).toBe(true)
})`)
