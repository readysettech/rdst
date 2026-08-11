import { tv } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Scrollable } from '@rs/ui-new/scrollable'
import { Text } from '@rs/ui-new/text'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { ActivityPulse } from '../components/audit/ActivityPulse'
import { BackgroundRuns } from '../components/BackgroundRuns'
import { DesktopUpdateControl } from '../components/DesktopUpdateControl'
import { ReportDialog } from '../components/ReportDialog'
import { TargetDropdown } from '../components/TargetDropdown'
import { TrialBalanceBadge } from '../components/TrialBalanceBadge'
import { TrialRegistrationDialog } from '../components/TrialRegistrationDialog'
import { useTarget } from '../hooks/useTarget'
import { useAuditSessionActive } from '../lib/auditSession'
import type { DesktopUpdateState } from '../lib/desktop'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { useSystemStatus } from '../lib/useSystemStatus'

// Plain-text acknowledgement of who is signed in; deliberately not a control.
function SidebarIdentity() {
  const { data } = useQuery({
    queryKey: ['settings', 'email'],
    queryFn: async () => {
      const response = await fetch('/api/settings/email')
      if (!response.ok) return null
      // Shared cache key with EmailReportDialog, so the shape carries
      // `verified` even though this identity line does not render it.
      return (await response.json()) as {
        email: string | null
        first_name: string | null
        last_name: string | null
        verified: boolean
      }
    },
    staleTime: 60_000,
  })
  if (!data?.email) return null
  return (
    <div className="px-3 py-1">
      <Text as="div" level="caption" className="truncate text-content-layout-3">
        {data.email}
      </Text>
    </div>
  )
}

const sidebarStyles = tv({
  base: [
    'w-80',
    'flex',
    'flex-col',
    'border-r border-border-layout-1',
    'left-0',
    'z-30',
    // Off-canvas below tablet; slides in when opened, always shown at tablet+
    // (responsive chrome, T19). No overflow trap — the frame degrades, it
    // doesn't clip.
    'transition-transform duration-200 ease-base',
    '-translate-x-full tablet:translate-x-0',
  ],
  variants: {
    isElectronMac: {
      true: [
        'absolute',
        'inset-y-0',
        'h-full',
        'bg-surface-layout-1',
        'border-border-layout-1',
      ],
      false: ['fixed', 'top-0', 'h-dvh', 'bg-surface-layout-1'],
    },
    mobileOpen: {
      // Overrides the base -translate-x-full at mobile; harmless at tablet+.
      true: ['translate-x-0'],
    },
  },
})

const navItemStyles = tv({
  base: [
    'flex',
    'items-center',
    'gap-2',
    'px-2',
    'py-1',
    'h-10',
    'rounded-lg',
    'text-sm',
    'font-medium',
    'text-content-layout-2',
    'transition-all',
    'duration-150',
    'w-full',
    'hover:bg-surface-layout-2',
    'hover:text-content-layout-1',
    'group',
  ],
  variants: {
    active: {
      true: [
        'bg-surface-primary-soft',
        'text-content-primary-soft',
        'hover:bg-surface-primary-soft-hover',
        'hover:text-content-primary-soft',
      ],
    },
  },
})

interface NavItem {
  label: string
  icon: IconStrokeName
  to: string
}

const homeItem: NavItem = { label: 'Home', icon: 'dashboard', to: '/' }

// The daily nav is a single flat list, top to bottom: Home, Ask, the Queries
// workspace, Performance tests, Health Check, then the demo. No group headers here —
// only Advanced gets one, since it's opt-in rather than daily-driver.
const primaryItems: NavItem[] = [
  { label: 'Ask', icon: 'sparkles', to: '/ask' },
  { label: 'Queries', icon: 'folder-file', to: '/queries' },
  { label: 'Performance tests', icon: 'speedometer', to: '/cache' },
  { label: 'Health check', icon: 'document-validation', to: '/audit' },
  { label: 'Try the demo', icon: 'querypilot', to: '/demo' },
]

const advancedItems: NavItem[] = [
  { label: 'Schema', icon: 'layers', to: '/schema' },
  { label: 'Code scan', icon: 'search', to: '/scan' },
  { label: 'Agents', icon: 'message-multiple', to: '/agents' },
  { label: 'Guards', icon: 'user-shield', to: '/guards' },
]

// Configuration recedes off the daily nav: after first connect the only global
// config control is the target switcher (top) plus a quiet footer "Settings"
// utility, never a top-level or Advanced nav item (configure-and-identity
// step 2 / T17). [USE-030, USE-034]
const settingsItem: NavItem = {
  label: 'Settings',
  icon: 'settings',
  to: '/configure',
}

const ADVANCED_STORAGE_KEY = 'rdst-sidebar-advanced'

function NavLink({
  item,
  active,
  running = false,
  onNavigate,
}: {
  item: NavItem
  active: boolean
  running?: boolean
  onNavigate?: () => void
}) {
  return (
    <Link
      to={item.to}
      className={navItemStyles({ active })}
      onClick={onNavigate}
    >
      <Icon
        name={item.icon}
        label={item.label}
        className={`w-4 h-4 transition-transform group-hover:scale-110 ${
          active ? 'text-content-primary-soft' : 'text-content-layout-3'
        }`}
      />
      <span>{item.label}</span>
      {running && (
        <span className="ml-auto">
          <ActivityPulse label={`${item.label} is running`} compact />
        </span>
      )}
    </Link>
  )
}

interface SidebarProps {
  isElectronMac?: boolean
  /** Open state for the off-canvas mobile drawer (<768px). */
  mobileOpen?: boolean
  /** Dismiss the mobile drawer (backdrop tap, nav click, Escape). */
  onMobileClose?: () => void
  desktopUpdateState?: DesktopUpdateState | null
  onInstallUpdate?: () => void
}

export function Sidebar({
  isElectronMac = false,
  mobileOpen = false,
  onMobileClose,
  desktopUpdateState = null,
  onInstallUpdate = () => undefined,
}: SidebarProps) {
  const router = useRouterState()
  const currentPath = router.location.pathname
  const { target: selectedTarget, setTarget: setSelectedTarget } = useTarget()
  const auditRunning = useAuditSessionActive()
  const [reportOpen, setReportOpen] = useState(false)
  const [trialOpen, setTrialOpen] = useState(false)
  const queryClient = useQueryClient()
  const [advancedOpen, setAdvancedOpen] = useState(
    () => localStorage.getItem(ADVANCED_STORAGE_KEY) === 'open'
  )

  const { data: status } = useSystemStatus()

  // Dismiss the off-canvas mobile drawer after any navigation (stable ref so
  // the effect keys only on the path, not the callback identity; skip the
  // mount pass — this closes on route CHANGE, not on first render).
  const closeRef = useRef(onMobileClose)
  closeRef.current = onMobileClose
  const isFirstPathRef = useRef(true)
  useEffect(() => {
    if (isFirstPathRef.current) {
      isFirstPathRef.current = false
      return
    }
    closeRef.current?.()
  }, [currentPath])

  // Mobile-drawer keyboard behavior (chrome prescription: the drawer is a
  // modal-like surface, so it must be fully keyboard-operable and must not
  // leave the dimmed background tabbable [USE-077, USE-090; VIS-030/034]):
  //  - Escape closes the drawer;
  //  - focus moves to the first nav item on open and returns to the invoking
  //    hamburger on close;
  //  - Tab is trapped within scrim + drawer while open.
  const asideRef = useRef<HTMLElement>(null)
  const scrimRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!mobileOpen) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    asideRef.current?.querySelector<HTMLElement>('nav a')?.focus()

    const focusables = (): HTMLElement[] => {
      const inAside = asideRef.current
        ? Array.from(
            asideRef.current.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )
          )
        : []
      return scrimRef.current ? [scrimRef.current, ...inAside] : inAside
    }

    const onKeyDown = (e: KeyboardEvent) => {
      // A dialog opened from the drawer (e.g. Give Feedback) brings its own
      // Radix focus trap — never fight it.
      if (
        (document.activeElement as HTMLElement | null)?.closest(
          '[role="dialog"]'
        )
      ) {
        return
      }
      if (e.key === 'Escape') {
        closeRef.current?.()
        return
      }
      if (e.key !== 'Tab') return
      const els = focusables()
      if (els.length === 0) return
      const active = document.activeElement as HTMLElement | null
      const idx = active ? els.indexOf(active) : -1
      if (idx === -1) {
        // Focus escaped (or never entered) — pull it back inside.
        e.preventDefault()
        ;(e.shiftKey ? els[els.length - 1] : els[0]).focus()
        return
      }
      if (e.shiftKey && idx === 0) {
        e.preventDefault()
        els[els.length - 1].focus()
      } else if (!e.shiftKey && idx === els.length - 1) {
        e.preventDefault()
        els[0].focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)

    // If the viewport grows past the tablet breakpoint while open, the sidebar
    // becomes static — drop the drawer state so the trap can't linger.
    let mq: MediaQueryList | undefined
    const onMqChange = () => {
      if (mq?.matches) closeRef.current?.()
    }
    if (typeof window.matchMedia === 'function') {
      mq = window.matchMedia('(min-width: 48rem)')
      mq.addEventListener('change', onMqChange)
    }

    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      mq?.removeEventListener('change', onMqChange)
      // Return focus to the invoking control (the hamburger).
      previouslyFocused?.focus?.()
    }
  }, [mobileOpen])

  const isActive = (item: NavItem) =>
    currentPath === item.to ||
    // The Queries workspace owns the analysis result route.
    (item.to === '/queries' && currentPath === '/results') ||
    // `/benchmark` remains as a compatibility route, but its navigation owner
    // is the unified Performance tests workspace.
    (item.to === '/cache' && currentPath === '/benchmark')

  // Keep the active item visible when landing directly on an advanced route.
  const advancedActive = advancedItems.some(isActive)
  const showAdvanced = advancedOpen || advancedActive

  const toggleAdvanced = () => {
    const next = !advancedOpen
    setAdvancedOpen(next)
    localStorage.setItem(ADVANCED_STORAGE_KEY, next ? 'open' : 'closed')
  }

  return (
    <>
      {/* Scrim behind the open mobile drawer; tap to dismiss. Tablet+ never
          shows it (the sidebar is always in-flow there). */}
      {mobileOpen && (
        <Pressable
          ref={scrimRef}
          type="button"
          aria-label="Close navigation"
          onClick={onMobileClose}
          className="fixed inset-0 z-20 bg-surface-scrim tablet:hidden"
        />
      )}
      <aside
        ref={asideRef}
        id="app-sidebar"
        className={sidebarStyles({ isElectronMac, mobileOpen })}
      >
        {/* On desktop mac the window traffic lights get their own draggable
          strip above the target selector. */}
        {isElectronMac && <div className="draggable-region h-8 shrink-0" />}

        {/* Target selector */}
        <div className="draggable-region h-14 border-b border-border-layout-1">
          <div className="no-drag h-full">
            <TargetDropdown
              selectedTarget={selectedTarget}
              onSelectTarget={setSelectedTarget}
            />
          </div>
        </div>

        {/* Navigation — persistent scrollbar so the Advanced group is
          discoverable/reachable below the fold at 1280×720. [QW2] */}
        <Scrollable className="flex-1" type="auto">
          {/* gap-2 BETWEEN groups > gap-1 WITHIN a group — spacing carries the
            grouping, one step up on the scale (design-system §1 [VIS-036]). */}
          <nav className="flex flex-col gap-2 p-2">
            <div className="flex flex-col gap-1">
              <NavLink
                key={homeItem.to}
                item={homeItem}
                active={isActive(homeItem)}
              />
              {primaryItems.map((item) => (
                <NavLink
                  key={item.to}
                  item={item}
                  active={isActive(item)}
                  running={item.to === '/audit' && auditRunning}
                />
              ))}
            </div>

            {/* Advanced group toggle — kept as a hand-roll: a quiet caption +
                chevron disclosure whose panel is the nav items rendered below,
                not inside a bordered container, so ui-new Disclosure's single
                trigger+panel box doesn't fit this nav grouping. */}
            <Pressable
              type="button"
              onClick={toggleAdvanced}
              className="flex items-center gap-1.5 px-3 pt-3 pb-1 cursor-pointer text-content-layout-3 hover:text-content-layout-2 transition-colors"
            >
              <Text
                level="caption"
                className="uppercase tracking-wider inherit"
              >
                Advanced
              </Text>
              <Icon
                name={showAdvanced ? 'chevron-down' : 'chevron-right'}
                label=""
                className="w-3 h-3"
              />
            </Pressable>
            {showAdvanced && (
              <>
                {advancedItems.map((item) => (
                  <NavLink key={item.to} item={item} active={isActive(item)} />
                ))}
                {/* Dev Settings is no longer a nav entry: its tools merged into
                    the Settings page (Developer settings section, /configure#dev).
                    The /dev-settings route now redirects there. [USE-097] */}
              </>
            )}
          </nav>
        </Scrollable>

        {/* Footer */}
        <div className="p-2 border-t border-border-layout-1 space-y-2">
          <SidebarIdentity />
          <BackgroundRuns />
          <TrialBalanceBadge />
          <Button
            type="button"
            label="Get free AI credits"
            icon="sparkles"
            iconPosition="left"
            modifier="outline"
            fullWidth
            onClick={() => setTrialOpen(true)}
            classMerge={navItemStyles({
              className:
                'cursor-pointer border border-border-primary-soft bg-gradient-to-r from-surface-primary-soft to-surface-info-soft text-content-primary-soft shadow-elevation-1 hover:shadow-elevation-2',
            })}
          />
          {/* Settings recedes here as a quiet utility, out of the daily nav. */}
          <NavLink item={settingsItem} active={isActive(settingsItem)} />
          {/* Give Feedback — kept as a hand-roll: it reuses navItemStyles so it
              reads as a sibling of the NavLinks above while opening a dialog
              rather than navigating; a ui-new Button would break that shared
              nav-item styling. */}
          <Pressable
            type="button"
            onClick={() => setReportOpen(true)}
            className={navItemStyles({ className: 'cursor-pointer' })}
          >
            {/* Distinct feedback glyph — no longer the Agents `message-multiple`
              (chrome prescription #9, VIS-008/010). */}
            <Icon
              name="customer-support"
              label="Give feedback"
              className="w-4 h-4 text-content-layout-3 group-hover:scale-110 transition-transform"
            />
            <span>Give feedback</span>
          </Pressable>

          {(status?.version || desktopUpdateState) && (
            <div className="flex items-center justify-between gap-2 px-3 py-2">
              {status?.version && (
                <div className="min-w-0" title={`v${status.version}`}>
                  <Text
                    level="caption"
                    className="truncate text-content-layout-3"
                  >
                    v{status.version}
                  </Text>
                </div>
              )}
              <div className="ml-auto">
                <DesktopUpdateControl
                  state={desktopUpdateState}
                  install={onInstallUpdate}
                />
              </div>
            </div>
          )}
        </div>

        <ReportDialog
          isOpen={reportOpen}
          onClose={() => setReportOpen(false)}
        />
        <TrialRegistrationDialog
          isOpen={trialOpen}
          onClose={() => setTrialOpen(false)}
          onSuccess={() => {
            void invalidateTrialRelatedQueries(queryClient)
            setTrialOpen(false)
          }}
        />
      </aside>
    </>
  )
}
