import { useCallback, useEffect, useRef, useState } from 'react'
import {
  readSelectedTarget,
  storeSelectedTarget,
  TARGET_CHANGED_EVENT,
  TARGET_STORAGE_KEY,
} from '../features/database/selectedTarget'
import { useSystemStatus } from '../lib/useSystemStatus'

/**
 * The selected database, shared by every page and tab. localStorage gives it on first
 * render; the backend's preferences, read once at load, are its source of truth.
 * @returns Object with target (string | null) and setTarget function
 */
export function useTarget() {
  const [target, setTargetState] = useState<string | null>(() =>
    typeof window !== 'undefined' ? readSelectedTarget() : null
  )

  useEffect(() => {
    if (typeof window === 'undefined') return
    // A change made before this subscription (another component's mount effect) is read
    // back here.
    setTargetState(readSelectedTarget())

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

  /** `automatic` marks a default the app filled in rather than the user's choice. */
  const setTarget = useCallback(
    (newTarget: string | null, automatic = false) => {
      const normalizedTarget = newTarget?.trim() || null
      setTargetState(normalizedTarget)
      storeSelectedTarget(normalizedTarget, automatic)
    },
    []
  )

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
    setTarget(current, true)
  }, [correctionKey, current, normalized, setTarget, refetch, dataUpdatedAt])

  return current
}
