import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
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
  '/analyze': { label: 'Analyze Query', icon: 'speedometer' },
  '/results': { label: 'Results', icon: 'speedometer', parent: '/analyze' },
  '/ask': { label: 'Ask', icon: 'sparkles' },
  '/top': { label: 'Slow Queries', icon: 'observe' },
  '/scan': { label: 'Code Scan', icon: 'search' },
  '/query-registry': { label: 'Saved Queries', icon: 'folder-file' },
  '/cache': { label: 'Caching', icon: 'database-settings' },
  '/benchmark': { label: 'Benchmark', icon: 'play' },
  '/schema': { label: 'Schema', icon: 'layers' },
  '/audit': { label: 'Health Check', icon: 'document-validation' },
  '/guards': { label: 'Guards', icon: 'user-shield' },
  '/agents': { label: 'Agents', icon: 'message-multiple' },
  '/fleet': { label: 'Fleet', icon: 'building' },
  '/readyset': { label: 'Readyset Testing', icon: 'test-tube' },
  '/configure': { label: 'Settings', icon: 'settings' },
  '/onboarding': { label: 'Get Started', icon: 'querypilot' },
  '/dev-settings': { label: 'Dev Settings', icon: 'adjustment-horizontal' },
  '/test': { label: 'Test', icon: 'adjustment-horizontal' },
}

interface HeaderProps {
  isElectronMac?: boolean
  isElectronLinux?: boolean
  /** Opens the off-canvas mobile nav drawer (<768px only). */
  onMenuClick?: () => void
  /** Drawer open state — drives the hamburger's aria-expanded [USE-090]. */
  mobileNavOpen?: boolean
}

export function Header({
  isElectronMac = false,
  isElectronLinux = false,
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
  const config =
    routeConfig[currentPath] ??
    (currentPath.startsWith('/audit/runs/') ? auditRunConfig : undefined)
  // An unknown path is a 404 (the branded notFoundComponent renders below the
  // breadcrumb). Show "Not Found" rather than a redundant "RDST › RDST" (QW7).
  const currentLabel = config?.label || 'Not Found'
  const currentIcon = config?.icon || 'search'
  const parentPath = config?.parent
  const parentConfig = parentPath ? routeConfig[parentPath] : null

  return (
    <header
      className={cn(
        'draggable-region',
        'h-14',
        isElectronMac
          ? 'bg-surface-layout-1/55 backdrop-blur-xl border-b border-border-layout-1/60'
          : 'bg-surface-layout-1/80 backdrop-blur-md border-b border-border-layout-1',
        // Sidebar offset only at tablet+; below that the sidebar is off-canvas.
        'tablet:pl-64',
        'sticky top-0 z-20'
      )}
    >
      <HStack className="px-6 h-full items-center justify-between">
        {/* Breadcrumb */}
        <HStack className="no-drag items-center gap-2">
          {/* Mobile-only hamburger to open the off-canvas nav (tablet+ hides
              it — the sidebar is always in view there). */}
          <button
            type="button"
            onClick={onMenuClick}
            aria-label="Open navigation"
            aria-expanded={mobileNavOpen}
            aria-controls="app-sidebar"
            className="no-drag tablet:hidden -ml-1 mr-1 flex h-8 w-8 items-center justify-center rounded-lg text-content-layout-2 hover:bg-surface-layout-2 hover:text-content-layout-1 transition-colors"
          >
            <Icon name="menu" label="Open navigation" className="w-5 h-5" />
          </button>
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

        {isElectronLinux && <WindowControls />}
      </HStack>
    </header>
  )
}
