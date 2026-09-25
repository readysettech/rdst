import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createServer } from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const { _electron } = await import(process.env.RDST_PLAYWRIGHT_MODULE ||
  '../../../node_modules/.pnpm/playwright@1.61.0/node_modules/playwright/index.mjs')

const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sourceDir = path.resolve(appDir, '../../../rdst')
const packaged = process.env.RDST_DESKTOP_EXECUTABLE
const electronBinary = packaged || path.join(appDir, 'node_modules/electron/dist/electron')
const display = process.env.DISPLAY

if (!display) {
  if (process.env.CI) throw new Error('Desktop E2E requires DISPLAY in CI')
  console.log('SKIP desktop E2E: DISPLAY is not set')
  process.exit(0)
}
try {
  execFileSync('xdpyinfo', ['-display', display], { stdio: 'ignore', timeout: 3000 })
} catch {
  if (process.env.CI) throw new Error('Desktop E2E display is unavailable in CI')
  console.log(`SKIP desktop E2E: display ${display} is unavailable`)
  process.exit(0)
}

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-desktop-e2e-'))
const dataDir = path.join(tempDir, 'data')
const homeDir = path.join(tempDir, 'home')
const configDir = path.join(homeDir, '.config')
fs.mkdirSync(dataDir, { recursive: true })
fs.mkdirSync(configDir, { recursive: true })

const server = createServer((_request, response) => {
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
  response.end('<!doctype html><html><head><title>RDST E2E</title></head><body>RDST E2E</body></html>')
})

function listen() {
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => resolve(server.address().port))
  })
}

async function bounded(operation, description, timeoutMs = 10000) {
  let timer
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Timed out: ${description} (${timeoutMs}ms)`)), timeoutMs)
      }),
    ])
  } finally { clearTimeout(timer) }
}

const fetchWithTimeout = (url, options = {}) => fetch(url, {
  ...options, signal: options.signal || AbortSignal.timeout(10000),
})

async function eventually(check, description, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const value = await bounded(Promise.resolve().then(check), description, Math.min(10000, Math.max(1, deadline - Date.now())))
      if (value) return value
    } catch (error) {
      lastError = error
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(`Timed out waiting for ${description}${lastError ? `: ${lastError.message}` : ''}`)
}

function isRunning(pid) {
  try {
    return !/^State:\s+Z/m.test(fs.readFileSync(`/proc/${pid}/status`, 'utf8'))
  } catch {
    return false
  }
}

function processTree(pid, found = new Set()) {
  if (!pid || found.has(pid) || !isRunning(pid)) return found
  found.add(pid)
  try {
    // uv can spawn the source backend from a worker thread. Inspect every thread's children.
    for (const thread of fs.readdirSync(`/proc/${pid}/task`)) {
      const children = fs.readFileSync(`/proc/${pid}/task/${thread}/children`, 'utf8').trim()
      for (const child of children.split(/\s+/).filter(Boolean)) processTree(Number(child), found)
    }
  } catch {
    // A process can exit while its children are being enumerated.
  }
  return found
}

function insertInboxEvent(title, subject = title, body = { message: 'Reconnect to resume monitoring.' }) {
  const script = `
import sqlite3, sys, time
db, title, subject, body = sys.argv[1:]
now = int(time.time())
with sqlite3.connect(db, timeout=5) as conn:
    cursor = conn.execute("INSERT INTO inbox_item (target,type,subject,state,severity,title,body_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", ("e2e", "monitoring_issue", subject, "new", 0.8, title, body, now, now))
    event = conn.execute("INSERT INTO inbox_event (item_id,ts,kind,detail_json) VALUES (?,?,?,?)", (cursor.lastrowid, now, "notified", "{}"))
    print(event.lastrowid)
`
  return Number(execFileSync('python3', ['-c', script, path.join(dataDir, 'monitor.db'), title, subject, JSON.stringify(body)], { encoding: 'utf8', timeout: 10000 }).trim())
}

let desktop
let backendPid
let descendants = new Set()
let exited = false
try {
  const port = await listen()
  const launchOptions = {
    executablePath: electronBinary,
    args: [...(packaged ? [] : [appDir]), '--no-sandbox', '--disable-gpu'],
    cwd: appDir,
    env: {
      ...process.env,
      HOME: homeDir,
      XDG_CONFIG_HOME: configDir,
      RDST_DATA_DIR: dataDir,
      RDST_TELEMETRY: 'off',
      RDST_DESKTOP_E2E_SOURCE_ROOT: packaged ? '' : sourceDir,
      RDST_DESKTOP_SMOKE: packaged ? '1' : '0',
      RDST_DESKTOP_LIFECYCLE: packaged ? '1' : '0',
      ELECTRON_RENDERER_URL: packaged ? '' : `http://127.0.0.1:${port}`,
    },
    timeout: 90000,
  }
  desktop = await _electron.launch(launchOptions)
  const snapshot = () => bounded(desktop.evaluate(() => globalThis.__rdstDesktopE2E?.snapshot()), 'desktop snapshot')
  const clickMenu = (label) => bounded(desktop.evaluate((_electron, value) => globalThis.__rdstDesktopE2E.clickMenu(value), label), `tray menu ${label}`)
  const ready = await eventually(async () => {
    const state = await snapshot()
    return state?.backendPid && state.trayExists ? state : null
  }, 'Electron tray and backend', 90000)
  backendPid = ready.backendPid
  descendants = processTree(backendPid)
  const running = await eventually(async () => {
    const state = await snapshot()
    return state.trayState === 'running' ? state : null
  }, 'tray running state')
  // A second tooltip line appears when the OS blocks notifications (a CI box may have no daemon).
  assert.equal(running.trayTooltip.split('\n')[0], 'RDST is running')
  assert.deepEqual(ready.menuItems.map((item) => item.label)
    .filter((label) => !/^Notifications (are blocked|can't be shown)/.test(label)), [
    'Open RDST', 'Inbox', 'Pause monitoring', 'Start at login', 'Notifications', 'Send test notification',
    'Open logs folder', 'Quit RDST',
  ])
  // The first launch sends the welcome toast that registers RDST with the OS.
  const welcome = await eventually(async () => (await snapshot()).notifications[0], 'welcome notification')
  assert.deepEqual(welcome, {
    title: 'RDST is running', body: "It'll let you know if a database needs you.", isElectronNotification: true,
  })
  const healthUrl = `${ready.backendUrl}/health`
  assert.equal((await fetchWithTimeout(healthUrl)).status, 200)
  console.log('PASS tray exists, tooltip and menu items match; source backend /health answers')

  assert.deepEqual(ready.startPhases.filter((phase) => phase !== 'migrating'), ['starting', 'ready'])
  if (!packaged) assert.ok(ready.backendLog.startsWith(configDir), ready.backendLog)
  const backendLog = fs.readFileSync(ready.backendLog, 'utf8')
  assert.match(backendLog, /\[stderr\] INFO: +Uvicorn running on/)
  assert.match(backendLog, /\[shell\] backend is ready/)
  console.log('PASS startup goes starting -> ready and the backend writes its output to the log file')

  const window = await bounded(desktop.firstWindow(), 'first window')
  await bounded(desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close()), 'hide window')
  await eventually(async () => !(await snapshot()).windowVisible, 'window hidden after close')
  assert.equal(isRunning(backendPid), true)
  assert.equal((await fetchWithTimeout(healthUrl)).status, 200)
  console.log('PASS closing the last window hides it and leaves the backend healthy')

  await clickMenu('Open RDST')
  await eventually(async () => (await snapshot()).windowVisible, 'window shown by tray')
  console.log('PASS Open RDST re-shows the window')

  await eventually(() => fs.existsSync(path.join(dataDir, 'monitor.db')), 'monitor database')
  const firstEventId = insertInboxEvent('E2E first notification')
  await eventually(async () => (await snapshot()).notifications.length === 2, 'first Electron notification')
  assert.ok((await snapshot()).lastInboxEventId >= firstEventId)
  const first = (await snapshot()).notifications[1]
  assert.deepEqual(first, {
    title: 'E2E first notification',
    body: 'Reconnect to resume monitoring.',
    isElectronNotification: true,
  })
  await bounded(desktop.evaluate(() => globalThis.__rdstDesktopE2E.clickNotification(1)), 'notification click')
  await eventually(() => /\/inbox\?item=\d+$/.test(window.url()), 'notification click opening the item')
  console.log('PASS notified SSE event creates Electron Notification; clicking opens the item in /inbox')

  const problem = await eventually(async () => {
    const state = await snapshot()
    return state.trayState === 'problem' ? state : null
  }, 'tray problem state for an Act now item')
  assert.equal(problem.trayTooltip.split('\n')[0], 'RDST - 1 item needs you')
  console.log('PASS an open Act now item turns the tray to its problem state')

  const lostEventId = insertInboxEvent("Can't reach the database", 'lost_connection', {
    target: 'e2e', failed_since: Math.floor(Date.now() / 1000) - 120,
    message: "RDST can't reach e2e. Monitoring is paused until it's back.",
  })
  await eventually(async () => (await snapshot()).notifications.length === 3, 'lost connection notification')
  assert.ok((await snapshot()).lastInboxEventId >= lostEventId)
  assert.deepEqual((await snapshot()).notifications[2], {
    title: "Can't reach e2e",
    body: "RDST can't reach e2e. Monitoring is paused until it's back.",
    isElectronNotification: true,
  })
  const unreachable = await eventually(async () => {
    const state = await snapshot()
    return state.trayTooltip.split('\n')[0] === "RDST - can't reach e2e" ? state : null
  }, 'tray naming the unreachable database')
  assert.equal(unreachable.trayState, 'problem')
  console.log('PASS a lost connection notifies "Can\'t reach e2e" and the tray names the database')

  await clickMenu('Notifications')
  assert.equal((await snapshot()).menuItems.find((item) => item.label === 'Notifications').checked, false)
  const secondEventId = insertInboxEvent('E2E suppressed notification')
  await eventually(async () => (await snapshot()).lastInboxEventId >= secondEventId, 'suppressed SSE event consumed')
  assert.equal((await snapshot()).notifications.length, 3)
  console.log('PASS notifications toggle suppresses the next notified event')

  const autostart = path.join(configDir, 'autostart', 'com.readyset.rdst-desktop.desktop')
  await clickMenu('Start at login')
  assert.equal((await snapshot()).menuItems.find((item) => item.label === 'Start at login').checked, true)
  assert.ok(fs.readFileSync(autostart, 'utf8').includes('--hidden'))
  await clickMenu('Start at login')
  assert.equal((await snapshot()).menuItems.find((item) => item.label === 'Start at login').checked, false)
  assert.equal(fs.existsSync(autostart), false)
  console.log('PASS Linux start-at-login setting round-trips through isolated XDG autostart')

  const api = async (route, body, method = 'GET') => {
    const response = await fetchWithTimeout(`${ready.backendUrl}/api/${route}`, {
      method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    assert.equal(response.ok, true, `${route}: HTTP ${response.status}`)
    const value = await response.json()
    assert.notEqual(value.success, false, route)
    return value
  }
  const target = process.env.RDST_DESKTOP_SMOKE_TARGET
    ? JSON.parse(process.env.RDST_DESKTOP_SMOKE_TARGET)
    : { host: '127.0.0.1', port: 1, database: 'e2e', user: 'e2e' }
  await api('configure/targets', { name: 'e2e', target }, 'POST')
  await api('monitor/targets/e2e/settings', { enabled: true }, 'PUT')
  await eventually(async () => {
    await bounded(desktop.evaluate(() => globalThis.__rdstDesktopE2E.refreshTray()), 'refresh tray')
    try { await clickMenu('Pause e2e'); return true } catch { return false }
  }, 'pause menu')
  await eventually(async () => !(await api('monitor/targets/e2e/settings')).enabled, 'monitor paused')
  await eventually(async () => {
    try { await clickMenu('Resume e2e (paused)'); return true } catch { return false }
  }, 'resume menu')
  await eventually(async () => (await api('monitor/targets/e2e/settings')).enabled, 'monitor resumed')
  const beforeClose = Math.floor(Date.now() / 1000)
  console.log('PASS tray pause/resume changes persisted monitor settings')

  await bounded(desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close()), 'hide window')
  if (process.env.RDST_DESKTOP_SMOKE_TARGET) {
    await eventually(async () => {
      const pulse = await api('monitor/targets/e2e/pulse')
      return pulse.pulses.some((entry) => entry.kind === 'collect' && entry.ok && entry.ts > beforeClose)
    }, 'collector reading with the window closed', 90000)
  }
  await clickMenu('Notifications')
  descendants = processTree(backendPid)
  for (const pid of [...descendants].reverse()) {
    try { process.kill(pid, 'SIGKILL') } catch (error) { if (error.code !== 'ESRCH') throw error }
  }
  await eventually(async () => (await snapshot()).trayState === 'offline', 'sidecar death reflected in tray')
  await eventually(async () => (await snapshot()).notifications.some((item) =>
    item.title === 'RDST stopped monitoring'), 'sidecar death notification')
  const crashedShell = desktop.process()
  await clickMenu('Quit RDST')
  await eventually(() => crashedShell.exitCode !== null, 'shell quit after sidecar death', 15000)
  desktop = await _electron.launch(launchOptions)
  const restarted = await eventually(async () => {
    const state = await snapshot()
    return state?.backendPid && state.backendPid !== backendPid ? state : null
  }, 'new sidecar after relaunch', 90000)
  backendPid = restarted.backendPid
  assert.equal((await fetchWithTimeout(`${restarted.backendUrl}/health`)).status, 200)
  const persisted = await fetchWithTimeout(`${restarted.backendUrl}/api/monitor/targets/e2e/settings`)
  assert.equal((await persisted.json()).enabled, true)
  console.log('PASS sidecar death alerts, relaunch restores a healthy backend and monitor settings')

  descendants = processTree(backendPid)
  const exit = new Promise((resolve, reject) => {
    const child = desktop.process()
    if (child.exitCode !== null) return resolve()
    child.once('exit', resolve)
    child.once('error', reject)
  })
  await clickMenu('Quit RDST')
  await bounded(exit, 'Electron quit', 15000)
  exited = true
  await eventually(() => [...descendants].every((pid) => !isRunning(pid)), 'backend process tree exit', 5000)
  await assert.rejects(fetchWithTimeout(`${restarted.backendUrl}/health`, { signal: AbortSignal.timeout(1000) }))
  console.log('PASS Quit stops the backend process tree and /health stops answering')
} finally {
  if (desktop && !exited) await bounded(desktop.close(), 'desktop teardown', 10000).catch((error) => {
    console.error(error.message)
    desktop.process().kill('SIGKILL')
  })
  for (const pid of descendants) {
    if (isRunning(pid)) process.kill(pid, 'SIGTERM')
  }
  server.closeAllConnections()
  await bounded(new Promise((resolve) => server.close(resolve)), 'test server teardown')
  fs.rmSync(tempDir, { recursive: true, force: true })
}
