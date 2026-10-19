import { readFileSync } from 'node:fs'

/**
 * The weekly quota of the Codex login behind CLIProxyAPI, as the proxy last
 * observed it on a response: percent used of the plan's weekly window, and
 * when it resets. It is the whole login's use, so anything else sharing the
 * login in the meantime counts too, and Codex reports whole percents.
 *
 *   node quota.ts    prints {used_percent, window_minutes, reset_at, observed_at}
 *
 * RDST_RELENTLESS_CLIPROXY_URL and RDST_RELENTLESS_CLIPROXY_MANAGEMENT_KEY
 * (or its _FILE) reach the proxy's management API.
 */

export interface Quota {
  used_percent: number
  window_minutes: number
  reset_at: string | null
  observed_at: string
}

interface AuthFile {
  disabled?: boolean
  quota?: { observed_at: string; signals?: Record<string, string> }
}

export async function weeklyQuota(): Promise<Quota | null> {
  const url = process.env.RDST_RELENTLESS_CLIPROXY_URL
  const file = process.env.RDST_RELENTLESS_CLIPROXY_MANAGEMENT_KEY_FILE
  const key = (
    file
      ? readFileSync(file, 'utf8')
      : (process.env.RDST_RELENTLESS_CLIPROXY_MANAGEMENT_KEY ?? '')
  ).trim()
  if (!url || !key) return null
  const response = await fetch(`${url}/v0/management/auth-files`, {
    headers: { Authorization: `Bearer ${key}` },
    signal: AbortSignal.timeout(15_000),
  })
  if (!response.ok) return null
  const { files } = (await response.json()) as { files: AuthFile[] }
  // The newest observation among the proxy's logins.
  let latest: Quota | null = null
  for (const { quota } of files.filter((f) => !f.disabled)) {
    const s = quota?.signals ?? {}
    if (!quota || Number(s['X-Codex-Primary-Window-Minutes']) !== 10_080)
      continue
    if (latest && latest.observed_at >= quota.observed_at) continue
    const reset = Number(s['X-Codex-Primary-Reset-At'])
    latest = {
      used_percent: Number(s['X-Codex-Primary-Used-Percent']),
      window_minutes: 10_080,
      reset_at: reset ? new Date(reset * 1000).toISOString() : null,
      observed_at: quota.observed_at,
    }
  }
  return latest
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.stdout.write(`${JSON.stringify(await weeklyQuota())}\n`)
}
