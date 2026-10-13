import { InlineNotice } from '@rs/ui-new/error-state'
import { useState } from 'react'
import { getDesktopNotifications } from '../lib/desktop'
import { useDesktopNotificationHealth } from '../lib/useDesktopNotificationHealth'

const HIDDEN_KEY = 'rdst_notifications_banner_hidden'

function readHidden(): boolean {
  try {
    return sessionStorage.getItem(HIDDEN_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * In the desktop app, says so when the OS drops RDST's notifications (a switched-off
 * Windows toast setting, macOS permission, no Linux notification service) and opens
 * the OS page that turns them on. RDST never changes the OS setting itself.
 */
export function DesktopNotificationsBanner() {
  const health = useDesktopNotificationHealth()
  const [hidden, setHidden] = useState(readHidden)
  if (!health?.blocked || !health.message || hidden) return null
  const hide = () => {
    try {
      sessionStorage.setItem(HIDDEN_KEY, '1')
    } catch {
      /* the banner comes back next launch */
    }
    setHidden(true)
  }
  return (
    <div data-testid="desktop-notifications-banner">
      <InlineNotice
        tone="quiet"
        errorClass="user-config"
        accent="warning"
        title="Notifications are off"
        message={health.message}
        trustworthy="The Inbox still lists every alert."
        action={
          health.settingsUrl
            ? {
                label: 'Turn on notifications',
                onClick: () => getDesktopNotifications()?.openSettings(),
              }
            : undefined
        }
        onRetry={hide}
        retryLabel="Hide"
      />
    </div>
  )
}
