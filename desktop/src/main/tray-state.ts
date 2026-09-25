// What the tray icon and its tooltip say: running, a problem that needs the person, monitoring
// turned off, or the bundled backend not answering. Pure, so the rules are testable.

export type TrayState = 'running' | 'problem' | 'paused' | 'offline'

export interface TrayInboxItem {
  target: string
  type: string
  subject: string
  state: string
  severity_band?: string
  muted_by?: number | null
}

export interface TrayStatusInput {
  /** False while the backend is starting, after it exits, or when it stops answering. */
  backendUp: boolean
  /** True while the backend is still starting for the first time. */
  starting?: boolean
  /** What starting means right now (upgrading data, retrying); defaults to "starting". */
  startingTooltip?: string | null
  /** Monitoring is off for all configured databases. */
  paused: boolean
  /** Open inbox items; null when the last read failed. */
  items: TrayInboxItem[] | null
}

export interface TrayStatus {
  state: TrayState
  tooltip: string
  /** Databases RDST can't reach, in name order. */
  unreachable: string[]
  /** Open, unmuted Act now items. */
  actNow: number
}

const UNREACHABLE_SUBJECTS = new Set(['lost_connection', 'password_missing'])
const CLOSED_STATES = new Set(['resolved', 'dismissed', 'snoozed'])

function isOpen(item: TrayInboxItem): boolean {
  return !CLOSED_STATES.has(item.state) && (item.muted_by ?? null) === null
}

export function isUnreachableItem(item: TrayInboxItem): boolean {
  return (
    item.type === 'monitoring_issue' && UNREACHABLE_SUBJECTS.has(item.subject)
  )
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

export function trayStatus(input: TrayStatusInput): TrayStatus {
  const open = (input.items ?? []).filter(isOpen)
  const unreachable = [
    ...new Set(open.filter(isUnreachableItem).map((item) => item.target)),
  ].sort()
  const actNow = open.filter((item) => item.severity_band === 'act_now').length
  if (!input.backendUp || input.items === null) {
    return {
      state: 'offline',
      tooltip: input.starting
        ? (input.startingTooltip ?? 'RDST - starting')
        : 'RDST - backend stopped',
      unreachable: [],
      actNow: 0,
    }
  }
  if (input.paused) {
    return {
      state: 'paused',
      tooltip: 'RDST - monitoring is off',
      unreachable,
      actNow,
    }
  }
  if (unreachable.length) {
    const tooltip =
      unreachable.length === 1
        ? `RDST - can't reach ${unreachable[0]}`
        : `RDST - can't reach ${plural(unreachable.length, 'database', 'databases')}`
    return { state: 'problem', tooltip, unreachable, actNow }
  }
  if (actNow) {
    return {
      state: 'problem',
      tooltip: `RDST - ${plural(actNow, 'item needs', 'items need')} you`,
      unreachable,
      actNow,
    }
  }
  return { state: 'running', tooltip: 'RDST is running', unreachable, actNow }
}

export interface TrayStatusWatcher {
  /** Read the inbox now (after an inbox event, a pause toggle, or a backend exit). */
  refresh: () => Promise<TrayStatus>
  stop: () => void
}

/** Polls the open inbox and reports each status change to `onChange`. */
export function watchTrayStatus(options: {
  apiBaseUrl: () => string | null
  paused: () => boolean | Promise<boolean>
  backendUp: () => boolean
  starting?: () => boolean
  startingTooltip?: () => string | null
  onChange: (status: TrayStatus) => void
  fetcher?: typeof fetch
  intervalMs?: number
}): TrayStatusWatcher {
  const fetcher = options.fetcher ?? fetch
  let last = ''
  let stopped = false
  let running: Promise<TrayStatus> | null = null
  let again = false

  async function readItems(): Promise<TrayInboxItem[] | null> {
    const base = options.apiBaseUrl()
    if (!base || !options.backendUp()) return null
    try {
      const response = await fetcher(`${base}/api/monitor/inbox?state=open`, {
        signal: AbortSignal.timeout(5_000),
      })
      if (!response.ok) return null
      const body = (await response.json()) as { items?: unknown }
      return Array.isArray(body.items) ? (body.items as TrayInboxItem[]) : null
    } catch {
      return null
    }
  }

  async function once(): Promise<TrayStatus> {
    let items = await readItems()
    let paused = false
    if (items !== null) {
      try {
        paused = await options.paused()
      } catch {
        items = null
      }
    }
    const status = trayStatus({
      backendUp: options.backendUp(),
      starting: options.starting?.(),
      startingTooltip: options.startingTooltip?.(),
      paused,
      items,
    })
    const key = JSON.stringify(status)
    if (!stopped && key !== last) {
      last = key
      options.onChange(status)
    }
    return status
  }

  function refresh(): Promise<TrayStatus> {
    if (running) {
      again = true
      return running
    }
    running = once().finally(() => {
      running = null
      if (again && !stopped) {
        again = false
        void refresh()
      }
    })
    return running
  }

  const timer = setInterval(() => {
    void refresh()
  }, options.intervalMs ?? 15_000)
  timer.unref?.()
  void refresh()
  return {
    refresh,
    stop: () => {
      stopped = true
      clearInterval(timer)
    },
  }
}
