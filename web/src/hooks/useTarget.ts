import { useCallback, useEffect, useState } from 'react'

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
