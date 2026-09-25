import { useEffect, useState } from 'react'

import {
  type DesktopNotificationHealth,
  getDesktopNotifications,
} from './desktop'

/** The desktop app's latest check of whether the OS shows RDST's notifications. */
export function useDesktopNotificationHealth(): DesktopNotificationHealth | null {
  const [health, setHealth] = useState<DesktopNotificationHealth | null>(null)
  useEffect(() => {
    const notifications = getDesktopNotifications()
    if (!notifications) return
    let mounted = true
    void notifications.getHealth().then((next) => {
      if (mounted) setHealth(next)
    })
    const unsubscribe = notifications.onHealthChange((next) => {
      if (mounted) setHealth(next)
    })
    return () => {
      mounted = false
      unsubscribe()
    }
  }, [])
  return health
}
