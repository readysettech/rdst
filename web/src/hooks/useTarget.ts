import { useCallback, useEffect, useRef, useState } from 'react'
import { useSystemStatus } from '../lib/useSystemStatus'

const TARGET_STORAGE_KEY = 'rdst_selected_target'
const TARGET_CHANGED_EVENT = 'rdst_target_changed'

/**
 * Hook for centralized target state management with localStorage sync
 * @returns Object with target (string | null) and setTarget function
 */
export function useTarget() {
  const [target, setTargetState] = useState<string | null>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem(TARGET_STORAGE_KEY)
    }
    return null
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    // A change made before this subscription (another component's mount effect) is read
    // back here.
    setTargetState(localStorage.getItem(TARGET_STORAGE_KEY))

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== TARGET_STORAGE_KEY) return
      setTargetState(event.newValue)
    }

    const handleTargetChanged = (event: Event) => {
      const customEvent = event as CustomEvent<string | null>
      setTargetState(customEvent.detail ?? null)
    }

    window.addEventListener('storage', handleStorage)
    window.addEventListener(
      TARGET_CHANGED_EVENT,
      handleTargetChanged as EventListener
    )

    return () => {
      window.removeEventListener('storage', handleStorage)
      window.removeEventListener(
        TARGET_CHANGED_EVENT,
        handleTargetChanged as EventListener
      )
    }
  }, [])

  const setTarget = useCallback((newTarget: string | null) => {
    const normalizedTarget = newTarget?.trim() ? newTarget : null
    setTargetState(normalizedTarget)

    if (typeof window !== 'undefined') {
      if (normalizedTarget) {
        localStorage.setItem(TARGET_STORAGE_KEY, normalizedTarget)
      } else {
        localStorage.removeItem(TARGET_STORAGE_KEY)
      }

      window.dispatchEvent(
        new CustomEvent<string | null>(TARGET_CHANGED_EVENT, {
          detail: normalizedTarget,
        })
      )
    }
  }, [])

  return { target, setTarget }
}

/**
 * `useTarget` plus the two facts a page needs before it can claim a target is
 * missing: the selection is read from localStorage synchronously, so an empty
 * value means nothing until the status request that supplies the default has
 * either landed or failed. [F-20, F-25]
 */
export function useTargetResolution() {
  const { target, setTarget } = useTarget()
  const status = useSystemStatus()
  return {
    target,
    setTarget,
    isResolving: !target && status.isPending,
    isUnavailable: !target && status.isError,
    refetch: status.refetch,
  }
}

/**
 * Keeps the selection on a configured database: a missing or stale name falls back to the
 * default database, else the first one. Returns the database the selection resolves to.
 *
 * The selection is shared by every open tab. A name this tab's list doesn't hold may be a
 * database another tab just added, so the list is read again before the selection is
 * overridden; overriding from a stale list made two tabs flip the selection back and forth.
 */
export function useDefaultTarget(): string | null {
  const { target: selected, setTarget } = useTarget()
  const { data: status, isLoading, refetch, dataUpdatedAt } = useSystemStatus()
  const targets = status?.targets ?? []
  const normalized = selected?.trim() || null
  const valid = normalized
    ? targets.some((target) => target.name === normalized)
    : false
  const fallback =
    status?.default_target &&
    targets.some((target) => target.name === status.default_target)
      ? status.default_target
      : (targets[0]?.name ?? null)
  // Without a list to check against, the selection stands.
  const current = !status || valid ? normalized : fallback

  // The last correction applied, so status refetches don't loop.
  const lastCorrection = useRef<string | null>(null)
  const verify = useRef<{ name: string; after: number } | null>(null)
  const correctionKey =
    !isLoading && status && current !== normalized
      ? `${normalized ?? '<none>'}=>${current ?? '<none>'}`
      : null

  useEffect(() => {
    if (!correctionKey) {
      lastCorrection.current = null
      verify.current = null
      return
    }
    if (lastCorrection.current === correctionKey) return
    if (normalized) {
      if (verify.current?.name !== normalized) {
        verify.current = { name: normalized, after: dataUpdatedAt }
        void refetch()
        return
      }
      if (dataUpdatedAt <= verify.current.after) return
    }
    lastCorrection.current = correctionKey
    setTarget(current)
  }, [correctionKey, current, normalized, setTarget, refetch, dataUpdatedAt])

  return current
}
