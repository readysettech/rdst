import { cn } from '@rs/tailwind-base'
import { ErrorState } from '@rs/ui-new/error-state'
import {
  createRootRoute,
  type ErrorComponentProps,
  Outlet,
  useNavigate,
} from '@tanstack/react-router'
import { type ReactNode, useState } from 'react'
// Direct import: the components barrel re-exports the SQL editor stack,
// which would statically pull CodeMirror into the eager entry chunk.
import { ConfigWarning } from '../components/ConfigWarning'
import { Header } from '../layout/Header'
import { Main } from '../layout/Main'
import { Sidebar } from '../layout/Sidebar'
import { isDesktopLinux, isDesktopMac } from '../lib/desktop'
import { useDesktopUpdates } from '../lib/useDesktopUpdates'

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
  const isElectronLinux = isDesktopLinux()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  return (
    <div
      className={cn(
        'relative h-dvh overflow-hidden',
        isElectronMac
          ? 'm-2 rounded-2xl border border-border-layout-1/70 bg-surface-layout-2/35 backdrop-blur-xl shadow-[0_20px_48px_rgba(0,0,0,0.35)]'
          : 'bg-surface-layout-2'
      )}
    >
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
        isElectronLinux={isElectronLinux}
        onMenuClick={() => setMobileNavOpen(true)}
        mobileNavOpen={mobileNavOpen}
      />
      <Sidebar
        isElectronMac={isElectronMac}
        mobileOpen={mobileNavOpen}
        onMobileClose={() => setMobileNavOpen(false)}
      />
      <Main isElectronMac={isElectronMac}>{children}</Main>
    </div>
  )
}

function RootComponent() {
  useDesktopUpdates()

  return (
    <AppShell>
      <ConfigWarning />
      <Outlet />
    </AppShell>
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
