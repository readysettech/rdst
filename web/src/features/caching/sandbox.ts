import { normalizeHttpError } from '../../lib/errorContract'

export interface SandboxDiagnostics {
  phase: string
  current_target?: string | null
  generation: number
  lease_purpose?: string | null
  queued_requests: number
  dirty_reason?: string | null
  failed_target?: string | null
  last_error?: string | null
  last_released_at?: string | null
  expires_at?: string | null
  container_name: string
  healthy: boolean
  docker_installed: boolean
  docker_running: boolean
}

export async function fetchSandboxDiagnostics(): Promise<SandboxDiagnostics> {
  const response = await fetch('/api/cache/sandbox')
  if (!response.ok) {
    const body = await response.json().catch(() => undefined)
    throw new Error(normalizeHttpError(response.status, body).message)
  }
  return response.json() as Promise<SandboxDiagnostics>
}

export async function queueSandboxPrewarm(target: string): Promise<void> {
  const response = await fetch('/api/cache/sandbox/prewarm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => undefined)
    throw new Error(normalizeHttpError(response.status, body).message)
  }
}
