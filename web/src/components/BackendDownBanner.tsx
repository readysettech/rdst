import { InlineNotice } from '@rs/ui-new/error-state'
import { useBackendState } from '../lib/backendReachability'
import { getDesktopOpenLogs, isDesktopRuntime } from '../lib/desktop'

/**
 * Over every page while RDST's backend is down, so pages showing what they had (or
 * nothing) aren't read as the databases' state. The desktop app restarts the backend.
 */
export function BackendDownBanner() {
  const state = useBackendState()
  if (state === 'running') return null
  const desktop = isDesktopRuntime()
  const openLogs = getDesktopOpenLogs()
  const failed = state === 'failed'
  return (
    <div className="pointer-events-none fixed inset-x-0 top-(--titlebar-height) z-50 flex justify-center px-4 pt-2">
      <div
        data-testid="backend-down-banner"
        className="pointer-events-auto w-full max-w-xl rounded-xl bg-surface-layout-2 shadow-elevation-2"
      >
        <InlineNotice
          errorClass="rdst-service"
          accent="warning"
          title={desktop ? 'RDST stopped. Restarting...' : 'RDST stopped'}
          message={
            failed
              ? 'It keeps stopping. RDST tries again every 30 seconds.'
              : 'Pages show what they had and update when RDST is back.'
          }
          action={
            failed && openLogs
              ? { label: 'Open logs', onClick: openLogs }
              : undefined
          }
        />
      </div>
    </div>
  )
}
