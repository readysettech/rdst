// Desktop notifications for inbox items the monitor marks `notified`. The choice is
// per browser (localStorage); the OS permission prompt runs when it is turned on.
import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'rdst_inbox_desktop_notifications'
const CHANGE_EVENT = 'rdst_inbox_desktop_notifications_changed'

export type DesktopNotificationSupport = 'unsupported' | 'denied' | 'ok'

export function desktopNotificationSupport(): DesktopNotificationSupport {
  if (typeof window === 'undefined' || !('Notification' in window))
    return 'unsupported'
  return window.Notification.permission === 'denied' ? 'denied' : 'ok'
}

export function desktopNotificationsEnabled(): boolean {
  try {
    return (
      window.localStorage.getItem(STORAGE_KEY) === '1' &&
      desktopNotificationSupport() === 'ok' &&
      window.Notification.permission === 'granted'
    )
  } catch {
    return false
  }
}

/** Turns desktop notifications on (asking the OS first) or off. Resolves to the new state. */
export async function setDesktopNotifications(on: boolean): Promise<boolean> {
  let enabled = false
  if (on && desktopNotificationSupport() === 'ok') {
    const permission =
      window.Notification.permission === 'granted'
        ? 'granted'
        : await window.Notification.requestPermission()
    enabled = permission === 'granted'
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  window.dispatchEvent(new Event(CHANGE_EVENT))
  return enabled
}

export function onDesktopNotificationsChange(listener: () => void) {
  window.addEventListener(CHANGE_EVENT, listener)
  return () => window.removeEventListener(CHANGE_EVENT, listener)
}

/** Push to this computer, as Settings and the Inbox bell both show it. */
export function useDesktopNotificationsOn(): boolean {
  return useSyncExternalStore(
    onDesktopNotificationsChange,
    desktopNotificationsEnabled,
    () => false
  )
}

export function showDesktopNotification(data: Record<string, unknown>) {
  if (!desktopNotificationsEnabled()) return
  const title = typeof data.title === 'string' ? data.title : 'New inbox alert'
  const target = typeof data.target === 'string' ? data.target : undefined
  // Stream rows are inbox events: `item_id` is the item, `id` the event.
  const id = typeof data.item_id === 'number' ? data.item_id : undefined
  try {
    const notification = new window.Notification(title, {
      body: target ? `On ${target}` : undefined,
      tag: id === undefined ? undefined : `rdst-inbox-${id}`,
    })
    notification.onclick = () => {
      window.focus()
      window.location.assign(id === undefined ? '/inbox' : `/inbox?item=${id}`)
    }
  } catch {
    /* some embedded browsers expose Notification but refuse to construct it */
  }
}
