import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  FleetProviderLoginStart,
  FleetProviderLoginStatus,
} from '../../lib/useFleet'

/** What the panel shows when a sign-in attempt fails, from any source. */
export interface ProviderLoginFailure {
  message: string
  code?: string
}

export function asProviderFailure(caught: unknown): ProviderLoginFailure {
  if (caught instanceof Error)
    return {
      message: caught.message,
      code: (caught as { code?: string }).code,
    }
  return { message: String(caught) }
}

export interface UseProviderOAuthLoginReturn {
  /** Set while a login is in flight; drives the "waiting for approval" copy. */
  loginId: string | null
  loginStatus: FleetProviderLoginStatus | null
  loginError: ProviderLoginFailure | null
  starting: boolean
  beginLogin: () => Promise<void>
  clearError: () => void
}

/**
 * Drive a provider's browser sign-in: open the authorize URL in a tab, then
 * poll the login until the provider reports success or failure.
 */
export function useProviderOAuthLogin({
  label,
  start,
  poll,
  onSuccess,
}: {
  /** Provider name, for the message a bare failed poll leaves behind. */
  label: string
  start: () => Promise<FleetProviderLoginStart>
  poll: (loginId: string) => Promise<FleetProviderLoginStatus>
  onSuccess: () => Promise<unknown>
}): UseProviderOAuthLoginReturn {
  const [loginId, setLoginId] = useState<string | null>(null)
  const [loginStatus, setLoginStatus] =
    useState<FleetProviderLoginStatus | null>(null)
  const [loginError, setLoginError] = useState<ProviderLoginFailure | null>(
    null
  )
  const [starting, setStarting] = useState(false)

  // The poll runs on a timer owned by one login attempt: keying it on anything
  // but the login id would restart the timer whenever a caller re-renders.
  const latest = useRef({ start, poll, onSuccess, label })
  useEffect(() => {
    latest.current = { start, poll, onSuccess, label }
  })

  const beginLogin = useCallback(async () => {
    setStarting(true)
    setLoginError(null)
    setLoginStatus(null)
    try {
      const started = await latest.current.start()
      window.open(started.authorize_url, '_blank')
      setLoginId(started.login_id)
      setLoginStatus({ state: 'running' })
    } catch (caught) {
      setLoginError(asProviderFailure(caught))
    } finally {
      setStarting(false)
    }
  }, [])

  useEffect(() => {
    if (!loginId) return
    let cancelled = false
    const runPoll = async () => {
      try {
        const status = await latest.current.poll(loginId)
        if (cancelled) return
        setLoginStatus(status)
        if (status.state === 'success') {
          setLoginId(null)
          await latest.current.onSuccess()
        } else if (status.state === 'failed') {
          setLoginId(null)
          setLoginError({
            message: status.detail || `${latest.current.label} sign-in failed.`,
          })
        }
      } catch (caught) {
        if (!cancelled) {
          setLoginId(null)
          setLoginError(asProviderFailure(caught))
        }
      }
    }
    void runPoll()
    const timer = window.setInterval(() => void runPoll(), 2_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [loginId])

  const clearError = useCallback(() => setLoginError(null), [])

  return { loginId, loginStatus, loginError, starting, beginLogin, clearError }
}
