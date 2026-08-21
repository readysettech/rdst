import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { Link, useRouterState } from '@tanstack/react-router'
import { WindowControls } from '../components/WindowControls'

interface RouteConfig {
  label: string
  icon:
    | 'speedometer'
    | 'sparkles'
    | 'observe'
    | 'search'
    | 'folder-file'
    | 'play'
    | 'layers'
    | 'test-tube'
    | 'settings'
    | 'database-settings'
    | 'document-validation'
    | 'dashboard'
    | 'user-shield'
    | 'message-multiple'
    | 'adjustment-horizontal'
    | 'building'
    | 'querypilot'
  parent?: string
}

const routeConfig: Record<string, RouteConfig> = {
  '/': { label: 'Home', icon: 'dashboard' },
  '/demo': { label: 'Demo', icon: 'querypilot' },
  '/queries': { label: 'Queries', icon: 'folder-file' },
  '/results': {
    label: 'Results',
    icon: 'speedometer',
    parent: '/queries',
  },
  '/ask': { label: 'Ask', icon: 'sparkles' },
  '/scan': { label: 'Code scan', icon: 'search' },
  '/cache': { label: 'Benchmarks', icon: 'speedometer' },
  '/schema': { label: 'Schema', icon: 'layers' },
  '/audit': { label: 'Health check', icon: 'document-validation' },
  '/guards': { label: 'Guards', icon: 'user-shield' },
  '/configure': { label: 'Settings', icon: 'settings' },
  '/onboarding': { label: 'Get started', icon: 'querypilot' },
  '/test': { label: 'Test', icon: 'adjustment-horizontal' },
  '/lab/performance-cards': {
    label: 'Performance cards lab',
    icon: 'test-tube',
    parent: '/cache',
  },
}

interface HeaderProps {
  isElectronMac?: boolean
  isElectronFrameless?: boolean
  /** Opens the off-canvas mobile nav drawer (<768px only). */
  onMenuClick?: () => void
  /** Drawer open state — drives the hamburger's aria-expanded [USE-090]. */
  mobileNavOpen?: boolean
}

export function Header({
  isElectronMac = false,
  isElectronFrameless = false,
  onMenuClick,
  mobileNavOpen = false,
}: HeaderProps) {
  const router = useRouterState()
  const currentPath = router.location.pathname
  // The saved-run detail route is dynamic (/audit/runs/$runId), so it has no
  // static entry — resolve it by prefix to a Health Check child crumb rather
  // than falling through to the 404 "Not Found" label.
  const auditRunConfig: RouteConfig = {
    label: 'Saved run',
    icon: 'document-validation',
    parent: '/audit',
  }
  const queriesLabConfig: RouteConfig = {
    label: `Queries lab ${currentPath.split('/').at(-1) ?? ''}`,
    icon: 'test-tube',
    parent: '/queries',
  }
  // Benchmarks is one page with two named tabs (B5) — the page identity names
  // the active one rather than staying generic, so "open Load test" resolves
  // to a findable breadcrumb.
  const cacheSearch = router.location.search as { view?: string }
  const cacheConfig: RouteConfig = {
    label: `Benchmarks - ${
      cacheSearch.view === 'load-test' ? 'Load test' : 'Compare'
    }`,
    icon: 'speedometer',
  }
  const config =
    (currentPath === '/cache' ? cacheConfig : routeConfig[currentPath]) ??
    (currentPath.startsWith('/audit/runs/')
      ? auditRunConfig
      : currentPath.startsWith('/lab/queries/')
        ? queriesLabConfig
        : undefined)
  // An unknown path is a 404 (the branded notFoundComponent renders below the
  // breadcrumb). Show "Not Found" rather than a redundant "RDST › RDST" (QW7).
  const currentLabel = config?.label || 'Not found'
  const currentIcon = config?.icon || 'search'
  const parentPath = config?.parent
  const parentConfig = parentPath ? routeConfig[parentPath] : null

  return (
    <header
      className={cn(
        'draggable-region',
        'h-14',
        isElectronMac
          ? 'bg-surface-layout-1 border-b border-border-layout-1'
          : 'bg-surface-layout-1/80 backdrop-blur-md border-b border-border-layout-1',
        // Sidebar offset only at tablet+; below that the sidebar is off-canvas.
        'tablet:pl-80',
        'sticky top-0 z-20'
      )}
    >
      <HStack className="px-6 h-full items-center justify-between">
        {/* Breadcrumb */}
        <HStack className="no-drag items-center gap-2">
          {/* Mobile-only hamburger to open the off-canvas nav (tablet+ hides
              it — the sidebar is always in view there). Kept as a hand-roll: it
              is bespoke app chrome — a nav toggle carrying aria-expanded /
              aria-controls for the drawer, not a design-system action button. */}
          <Pressable
            type="button"
            onClick={onMenuClick}
            aria-label="Open navigation"
            aria-expanded={mobileNavOpen}
            aria-controls="app-sidebar"
            className="no-drag tablet:hidden -ml-1 mr-1 flex h-8 w-8 items-center justify-center rounded-lg text-content-layout-2 hover:bg-surface-layout-2 hover:text-content-layout-1 transition-colors"
          >
            <Icon name="menu" label="Open navigation" className="w-5 h-5" />
          </Pressable>
          <Link
            to="/"
            className="text-content-layout-3 hover:text-content-layout-1 transition-colors"
          >
            <Text level="label-small" className="font-semibold tracking-wide">
              RDST
            </Text>
          </Link>

          <Icon
            name="chevron-right"
            label="separator"
            className="w-3.5 h-3.5 text-content-layout-3"
          />

          {parentConfig && parentPath && (
            <>
              <Link
                to={parentPath}
                className="text-content-layout-3 hover:text-content-layout-1 transition-colors"
              >
                <Text level="label-small">{parentConfig.label}</Text>
              </Link>
              <Icon
                name="chevron-right"
                label="separator"
                className="w-3.5 h-3.5 text-content-layout-3"
              />
            </>
          )}

          <HStack className="items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center">
              <Icon
                name={currentIcon}
                label={currentLabel}
                className="w-3.5 h-3.5 text-content-primary-soft"
              />
            </div>
            <Text
              level="label-small"
              className="text-content-layout-1 font-medium"
            >
              {currentLabel}
            </Text>
          </HStack>
        </HStack>

        {isElectronFrameless && <WindowControls />}
      </HStack>
    </header>
  )
}
