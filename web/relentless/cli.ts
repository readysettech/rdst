import { resolve } from 'node:path'

import type { Mode } from './driver/app.ts'
import { call, startDriver } from './driver/client.ts'
import type { CommandInput } from './driver/session.ts'

/**
 * `rt <command> [args] [--flag value]`: drive the application a relentless
 * run owns. Arguments after a bare `--` are always positional, so text that
 * starts with dashes can still be typed.
 */
function parse(argv: string[]): { name: string; input: CommandInput } {
  const [name = 'help', ...rest] = argv
  const input: CommandInput = { pos: [], flags: {} }
  for (let i = 0; i < rest.length; i += 1) {
    const token = rest[i]
    if (token === '--') {
      input.pos.push(...rest.slice(i + 1))
      break
    }
    if (token.startsWith('--') && token.length > 2) {
      const [key, inline] = token.slice(2).split(/=(.*)/s, 2)
      if (inline !== undefined) input.flags[key] = inline
      else if (i + 1 < rest.length && !rest[i + 1].startsWith('--')) {
        input.flags[key] = rest[++i]
      } else input.flags[key] = true
      continue
    }
    input.pos.push(token)
  }
  return { name, input }
}

const USAGE = `rt <command>
  start --mode browser|desktop [--desktop-exec PATH] [--headed] [--home-from DIR]
  status | stop
  snapshot [TARGET] [--depth N] [--max-chars N]
  screenshot [TARGET] [--label NAME] [--full-page]
  text [TARGET] [--max-chars N]
  goto PATH | reload | back | forward | kill-backend
  restart [--offline | --online]    relaunch; --offline makes the internet unreachable
  upload TARGET FILE                a file from the run directory, e.g. fixtures/targets.csv
  deeplink URL                      desktop: a second instance opened with rdst:// URL
  crash-renderer                    crash the page's renderer process
  click TARGET [--double] [--button right] | hover TARGET
  fill TARGET TEXT | type TARGET TEXT | press KEY [--target TARGET]
  fill-secret TARGET anthropic-api-key   (types a value you never see)
  select TARGET VALUE | check TARGET | uncheck TARGET
  wait [--selector TARGET [--state visible|hidden|attached|detached]]
       [--text TEXT] [--url SUBSTRING] [--api PATH_SUBSTRING] [--timeout MS]
  viewport WIDTH HEIGHT
  eval JS_EXPRESSION
  api METHOD /api/PATH [--json BODY]
  evidence [--since SEQ | --all] [--kinds console,page_error,request_failed,api,stream,lifecycle]
  logs [--last N] [--grep REGEX]
  tab list | tab new [PATH] | tab switch N | tab close N
  menu | menu "View > Actual Size"        (desktop: list or click the native menu)
  tray | tray "Monitoring > Pause all"    (desktop: list or click the tray icon's menu)
  db list | db exec postgres|mysql [--admin] -- SQL | db pause|resume postgres|mysql|bastion
  mail address [LABEL] | mail read ADDRESS [--contains X] | mail open ADDRESS [--prefix URL] [--index N]
  finding --json '{"title","severity":"P1-P4","expected","actual","steps":[...],...}'
  phase ID setup|action|observe|recover|cleanup      where a mission is inside a flow
  flow ID --status passed|failed|blocked --summary TEXT   a mission's flow outcome
  finish --status passed|failed|blocked --summary TEXT
TARGET is a ref from snapshot (e12) or a Playwright selector
(role=button[name="Save"], text=Save, [name=host], #main-content).`

async function main() {
  const { name, input } = parse(process.argv.slice(2))
  if (name === 'help' || name === '--help') {
    console.log(USAGE)
    return 0
  }
  const runDirFlag = input.flags['run-dir']
  const runDir = resolve(
    typeof runDirFlag === 'string'
      ? runDirFlag
      : (process.env.RDST_RELENTLESS_RUN_DIR ?? '')
  )
  if (!process.env.RDST_RELENTLESS_RUN_DIR && typeof runDirFlag !== 'string') {
    console.log(
      JSON.stringify({
        ok: false,
        error: 'set RDST_RELENTLESS_RUN_DIR or pass --run-dir',
      })
    )
    return 2
  }
  if (name === 'start') {
    try {
      await startDriver({
        runDir,
        mode: (input.flags.mode as Mode) ?? 'browser',
        desktopExecutable:
          typeof input.flags['desktop-exec'] === 'string'
            ? input.flags['desktop-exec']
            : undefined,
        headed: input.flags.headed === true,
        homeFrom:
          typeof input.flags['home-from'] === 'string'
            ? input.flags['home-from']
            : undefined,
      })
    } catch (error) {
      console.log(
        JSON.stringify({ ok: false, error: String(error), kind: 'infra' })
      )
      return 1
    }
    const status = await call(runDir, 'status')
    console.log(JSON.stringify(status, null, 2))
    return status.ok ? 0 : 1
  }
  const response = await call(runDir, name, input)
  console.log(JSON.stringify(response, null, 2))
  return response.ok ? 0 : 1
}

process.exitCode = await main()
