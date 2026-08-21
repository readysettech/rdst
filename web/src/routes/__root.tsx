import { Button } from '@rs/ui-new/button'
import { ErrorState } from '@rs/ui-new/error-state'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import {
  createRootRoute,
  type ErrorComponentProps,
  Outlet,
  useNavigate,
} from '@tanstack/react-router'
import { type ReactNode, useState } from 'react'
import { ActivityPulse } from '../components/audit/ActivityPulse'
// Direct import: the components barrel re-exports the SQL editor stack,
// which would statically pull CodeMirror into the eager entry chunk.
import { ConfigWarning } from '../components/ConfigWarning'
import { SetupGuide } from '../features/setup/SetupGuide'
import { useTarget } from '../hooks/useTarget'
import { Header } from '../layout/Header'
import { Main } from '../layout/Main'
import { Sidebar } from '../layout/Sidebar'
import {
  cancelActiveAudit,
  requestAuditRunView,
  useAuditPresentation,
  useAuditSession,
} from '../lib/auditSession'
import { isDesktopFrameless, isDesktopMac } from '../lib/desktop'
import { useDesktopUpdates } from '../lib/useDesktopUpdates'
import { useQueryDiscoveryTransport } from '../lib/useQueryDiscovery'

export const Route = createRootRoute({
  component: RootComponent,
  errorComponent: RootErrorBoundary,
  notFoundComponent: RootNotFound,
})

/**
 * The app shell (chrome). Rendered by RootComponent around normal routes, and
 * by RootErrorBoundary — which REPLACES RootComponent when the root boundary
 * catches, so it must bring its own shell to avoid a chrome-less screen (B1).
 * RootNotFound must NOT render it: a root `notFoundComponent` is rendered in
 * place of the root's <Outlet />, inside the still-mounted RootComponent, so
 * wrapping it in AppShell would double the header/breadcrumb (owner-reported).
 */
function AppShell({ children }: { children: ReactNode }) {
  const isElectronMac = isDesktopMac()
  const isElectronFrameless = isDesktopFrameless()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const { state: desktopUpdateState, install: installDesktopUpdate } =
    useDesktopUpdates()

  return (
    <div className="relative h-dvh overflow-hidden bg-surface-layout-2">
      {/* Skip-to-content: the first focusable element, hidden until focused
          (USE-089). Jumps keyboard users past the chrome to the content. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface-overlay focus:px-4 focus:py-2 focus:text-label-small focus:text-content-layout-1 focus:shadow-elevation-2"
      >
        Skip to content
      </a>
      <Header
        isElectronMac={isElectronMac}
        isElectronFrameless={isElectronFrameless}
        onMenuClick={() => setMobileNavOpen(true)}
        mobileNavOpen={mobileNavOpen}
      />
      <Sidebar
        isElectronMac={isElectronMac}
        mobileOpen={mobileNavOpen}
        onMobileClose={() => setMobileNavOpen(false)}
        desktopUpdateState={desktopUpdateState}
        onInstallUpdate={installDesktopUpdate}
      />
      <Main>{children}</Main>
      {/* Floating, fixed-position, and suppressed on the routes that own the
          bottom-right corner — it costs the shell no layout. */}
      <SetupGuide />
    </div>
  )
}

function RootComponent() {
  const { target } = useTarget()
  useQueryDiscoveryTransport(target)

  return (
    <AppShell>
      <ConfigWarning />
      <AuditRunBanner />
      <Outlet />
    </AppShell>
  )
}

function AuditRunBanner() {
  const navigate = useNavigate()
  const active = useAuditSession()
  const { completed, runViewVisible } = useAuditPresentation()
  const session = active ?? completed
  if (!session || runViewVisible) return null
  const targetLabel =
    'targetNames' in session && session.targetNames.length === 1
      ? session.targetNames[0]
      : 'targetNames' in session && session.targetNames.length > 1
        ? `${session.targetNames.length} targets`
        : session.targetLabel

  const view = () => {
    requestAuditRunView()
    void navigate({ to: '/audit' })
  }

  return (
    <HStack className="sticky top-0 z-30 mb-4 justify-between items-center gap-3 rounded-xl border border-border-primary-soft bg-surface-primary-soft px-4 py-2.5 shadow-elevation-1 flex-wrap">
      <HStack className="gap-2 items-center">
        {active ? (
          <ActivityPulse label="Health check running" />
        ) : (
          <span className="text-content-positive-soft">✓</span>
        )}
        <Text level="body-small" className="text-content-layout-1">
          Health check {active ? 'running' : 'complete'} on {targetLabel}
        </Text>
      </HStack>
      <HStack className="gap-2 items-center">
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="View"
          onClick={view}
        />
        {active && (
          <Button
            variant="negative"
            modifier="ghost"
            size="small"
            label="Cancel"
            onClick={cancelActiveAudit}
          />
        )}
      </HStack>
    </HStack>
  )
}

function RootErrorBoundary({ error, reset }: ErrorComponentProps) {
  const navigate = useNavigate()
  return (
    <AppShell>
      <ErrorState
        layout="page"
        eyebrow="Error"
        errorClass="database"
        icon="alert"
        title="Something went wrong"
        message="This screen ran into an unexpected error. Your data is safe — try again, or head back home."
        action={{
          label: 'Back to Home',
          onClick: () => navigate({ to: '/' }),
        }}
        onRetry={reset}
        detail={error instanceof Error ? error.message : String(error)}
      />
    </AppShell>
  )
}

function RootNotFound() {
  const navigate = useNavigate()
  // No AppShell here: the router renders this in place of the root <Outlet />,
  // so RootComponent's shell (header/sidebar) is already on screen.
  return (
    <ErrorState
      layout="page"
      eyebrow="404"
      errorClass="valid-negative"
      icon="search"
      title="Page not found"
      message="The page you're looking for doesn't exist or may have moved."
      action={{
        label: 'Back to Home',
        onClick: () => navigate({ to: '/' }),
      }}
    />
  )
}
