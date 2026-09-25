/** A configured database and its backend monitoring setting. */
export interface TrayDatabase {
  name: string
  /** Monitoring is on. */
  enabled: boolean
}

export type PauseAction =
  | { kind: 'pause' | 'resume'; target: string }
  | { kind: 'pause-all' }
  | { kind: 'resume-all' }

export interface PauseMenuItem {
  label: string
  enabled: boolean
  action?: PauseAction
  separator?: boolean
}

/**
 * The tray's monitoring submenu: one entry per database, then controls for all targets.
 * `databases` is null until the backend has answered.
 */
export function pauseMenuModel(
  databases: TrayDatabase[] | null
): PauseMenuItem[] {
  if (databases === null)
    return [{ label: 'Loading databases', enabled: false }]
  if (databases.length === 0)
    return [{ label: 'No databases are monitored', enabled: false }]
  const sorted = [...databases].sort((a, b) => a.name.localeCompare(b.name))
  const running = sorted.some((database) => database.enabled)
  const paused = sorted.some((database) => !database.enabled)
  return [
    ...sorted.map(
      (database): PauseMenuItem =>
        database.enabled
          ? {
              label: `Turn monitoring off for ${database.name}`,
              enabled: true,
              action: { kind: 'pause', target: database.name },
            }
          : {
              label: `Turn monitoring on for ${database.name}`,
              enabled: true,
              action: { kind: 'resume', target: database.name },
            }
    ),
    { label: '', enabled: false, separator: true },
    {
      label: 'Turn all monitoring off',
      enabled: running,
      action: { kind: 'pause-all' },
    },
    ...(paused
      ? [
          {
            label: 'Turn all monitoring on',
            enabled: true,
            action: { kind: 'resume-all' },
          } as PauseMenuItem,
        ]
      : []),
  ]
}

/** True when monitoring is off for every configured database. */
export function allPaused(databases: TrayDatabase[] | null): boolean {
  return (
    databases !== null &&
    databases.length > 0 &&
    databases.every((database) => !database.enabled)
  )
}

async function jsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok)
    throw new Error(`Monitor API returned HTTP ${response.status}`)
  return (await response.json()) as T
}

const settingsUrl = (apiBaseUrl: string, name: string) =>
  `${apiBaseUrl}/api/monitor/targets/${encodeURIComponent(name)}/settings`

/** Read monitoring settings for every configured database. */
export async function listTrayDatabases(
  apiBaseUrl: string,
  fetcher: typeof fetch = fetch
): Promise<TrayDatabase[]> {
  const list = await jsonResponse<{ targets: { name: string }[] }>(
    await fetcher(`${apiBaseUrl}/api/configure/targets`, {
      signal: AbortSignal.timeout(5_000),
    })
  )
  const databases: TrayDatabase[] = []
  for (const { name } of list.targets) {
    const current = await jsonResponse<{ enabled: boolean }>(
      await fetcher(settingsUrl(apiBaseUrl, name), {
        signal: AbortSignal.timeout(5_000),
      })
    )
    databases.push({ name, enabled: current.enabled })
  }
  return databases
}

async function setMonitoring(
  apiBaseUrl: string,
  name: string,
  enabled: boolean,
  fetcher: typeof fetch
): Promise<void> {
  const response = await fetcher(settingsUrl(apiBaseUrl, name), {
    signal: AbortSignal.timeout(5_000),
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!response.ok) {
    throw new Error(
      `Could not turn monitoring ${enabled ? 'on' : 'off'} for ${name}: HTTP ${response.status}`
    )
  }
}

/** Run one submenu entry against the databases it was built from. */
export async function applyPauseAction(
  apiBaseUrl: string,
  action: PauseAction,
  databases: TrayDatabase[],
  fetcher: typeof fetch = fetch
): Promise<void> {
  if (action.kind === 'pause' || action.kind === 'resume') {
    await setMonitoring(
      apiBaseUrl,
      action.target,
      action.kind === 'resume',
      fetcher
    )
    return
  }
  const resume = action.kind === 'resume-all'
  for (const database of databases) {
    if (database.enabled === resume) continue
    await setMonitoring(apiBaseUrl, database.name, resume, fetcher)
  }
}
