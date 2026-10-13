import { ErrorState } from '@rs/ui-new/error-state'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageContainer } from '@rs/ui-new/page-container'
import { PageNotices, PageNoticesProvider } from '@rs/ui-new/page-notices'
import { useQuery } from '@tanstack/react-query'
import {
  createRootRoute,
  type ErrorComponentProps,
  Outlet,
  useNavigate,
  useRouterState,
} from '@tanstack/react-router'
import { type ReactNode, useEffect } from 'react'
import { AddDatabaseHost } from '@/components/configure/AddDatabaseDialog'
import { AiAccessBoundary } from '../components/AiAccessBoundary'
// Direct import: the components barrel re-exports the SQL editor stack,
// which would statically pull CodeMirror into the eager entry chunk.
import { ConfigWarning } from '../components/ConfigWarning'
import { DesktopNotificationsBanner } from '../components/DesktopNotificationsBanner'
import { WindowControls } from '../components/WindowControls'
import { useTarget } from '../hooks/useTarget'
import { useGoShortcuts } from '../layout/goShortcuts'
import { legacyTitle, Main } from '../layout/Main'
import { type RouteLayout, useRouteLayout } from '../layout/routeLayout'
import { Sidebar } from '../layout/Sidebar'
import { setAnalyticsAccountId } from '../lib/analytics'
import { fetchAccountStatus } from '../lib/api'
import { reportAppError } from '../lib/appErrors'
import { isDesktopFrameless, isDesktopMac } from '../lib/desktop'
import { useQueryDiscoveryTransport } from '../lib/useQueryDiscovery'

export const Route = createRootRoute({
  component: RootComponent,
  errorComponent: RootErrorBoundary,
  notFoundComponent: RootNotFound,
})

/**
 * The app shell: the sidebar flush on the page tone and the canvas beside it.
 * Rendered by RootComponent around normal routes, and by RootErrorBoundary,
 * which REPLACES RootComponent when the root boundary catches, so it must
 * bring its own shell. RootNotFound must NOT render it: a root
 * `notFoundComponent` is rendered in place of the root's <Outlet />, inside
 * the still-mounted RootComponent. A `bare` route (sign-in) gets no shell.
 * `layout` overrides the route's own framing.
 */
function AppShell({
  children,
  layout: layoutOverride,
}: {
  children: ReactNode
  layout?: RouteLayout
}) {
  const isElectronMac = isDesktopMac()
  const isElectronFrameless = isDesktopFrameless()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const routeLayout = useRouteLayout()
  const layout = layoutOverride ?? routeLayout
  useGoShortcuts()

  if (layout === 'bare') return <>{children}</>

  return (
    <div className="relative flex h-dvh overflow-hidden bg-surface-layout-2">
      {/* Skip-to-content: the first focusable element, hidden until focused. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface-overlay focus:px-4 focus:py-2 focus:text-label-small focus:text-content-layout-1 focus:shadow-elevation-2"
      >
        Skip to content
      </a>
      {isElectronFrameless && (
        <div className="draggable-region fixed top-0 right-0 z-40">
          <WindowControls />
        </div>
      )}
      <Sidebar isElectronMac={isElectronMac} />
      <Main
        layout={layout}
        scrollKey={pathname}
        fallbackTitle={legacyTitle(pathname)}
      >
        {children}
      </Main>
    </div>
  )
}

function RootComponent() {
  return (
    <AiAccessBoundary
      frame={(gate) => <AppShell layout="page">{gate}</AppShell>}
    >
      <ReadyRoot />
    </AiAccessBoundary>
  )
}

function ReadyRoot() {
  const { target } = useTarget()
  useQueryDiscoveryTransport(target)
  const accountStatus = useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
  })

  useEffect(() => {
    setAnalyticsAccountId(accountStatus.data?.analytics_account_id)
  }, [accountStatus.data?.analytics_account_id])

  // The global notices show under the PageBar of the page on screen.
  return (
    <PageNoticesProvider>
      <AppShell>
        <PageNotices>
          <DesktopNotificationsBanner />
          <ConfigWarning />
        </PageNotices>
        <Outlet />
        {/* Outside the notice slot: pages without a PageBar open it too. */}
        <AddDatabaseHost />
      </AppShell>
    </PageNoticesProvider>
  )
}

function RootErrorBoundary({ error, reset }: ErrorComponentProps) {
  const navigate = useNavigate()
  useEffect(() => reportAppError('error_boundary', error), [error])
  return (
    <AppShell layout="page">
      <PageBar title="Something went wrong" />
      <PageContainer className="pt-9 pb-24">
        <ErrorState
          tone="quiet"
          errorClass="database"
          title=""
          message="This screen ran into an unexpected error. Your data is safe."
          action={{
            label: 'Back to Home',
            onClick: () => navigate({ to: '/' }),
          }}
          onRetry={reset}
          detail={error instanceof Error ? error.message : String(error)}
        />
      </PageContainer>
    </AppShell>
  )
}

function RootNotFound() {
  const navigate = useNavigate()
  // No AppShell here: the router renders this in place of the root <Outlet />,
  // so RootComponent's shell, with its "Not found" bar, is already on screen.
  return (
    <ErrorState
      tone="quiet"
      errorClass="valid-negative"
      titleAs="h1"
      title="Page not found"
      message="The page you're looking for doesn't exist or may have moved."
      action={{
        label: 'Back to Home',
        onClick: () => navigate({ to: '/' }),
      }}
    />
  )
}
