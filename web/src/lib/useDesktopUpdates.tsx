import { useCallback, useEffect, useState } from 'react'

import { type DesktopUpdateState, getDesktopUpdates } from './desktop'

/**
 * Subscribes to desktop-shell update state. The shell owns checking,
 * downloading, and installation; the renderer only presents progress and
 * forwards the final restart action.
 */
export function useDesktopUpdates(): {
  state: DesktopUpdateState | null
  install: () => void
} {
  const [state, setState] = useState<DesktopUpdateState | null>(null)

  useEffect(() => {
    const updates = getDesktopUpdates()
    if (!updates) return

    let mounted = true
    void updates.getState().then((nextState) => {
      if (mounted) setState(nextState)
    })
    const unsubscribe = updates.onStateChange((nextState) => {
      if (mounted) setState(nextState)
    })

    return () => {
      mounted = false
      unsubscribe()
    }
  }, [])

  const install = useCallback(() => getDesktopUpdates()?.install(), [])

  return { state, install }
}
