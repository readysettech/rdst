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
import { SetupGuideHelpEntry } from '../features/setup/SetupGuideHelpEntry'
import { useTarget } from '../hooks/useTarget'
import { trackEvent } from '../lib/analytics'
import { useAuditSessionActive } from '../lib/auditSession'
import type { DesktopUpdateState } from '../lib/desktop'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { useSystemStatus } from '../lib/useSystemStatus'
import { VALUE_PROPOSITION } from '../lib/valueProposition'

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
    'rounded-lg',
    'text-sm',
    'text-content-layout-2',
    'transition-all',
    'duration-150',
    'w-full',
    'hover:bg-surface-layout-2',
    'hover:text-content-layout-1',
    'focus-visible:outline-none',
    'focus-visible:ring-2',
    'focus-visible:ring-border-primary-soft',
    'focus-visible:ring-offset-2',
    'focus-visible:ring-offset-surface-layout-1',
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
    // Daily nav vs. footer utility (F5): utilities sit visibly subordinate to
    // the primary list — shorter and normal weight, same alignment and hover.
    size: {
      nav: ['h-10', 'font-medium'],
      footer: ['h-8', 'font-normal'],
    },
  },
  defaultVariants: {
    size: 'nav',
  },
})

interface NavItem {
  label: string
  icon: IconStrokeName
  to: string
}

const homeItem: NavItem = { label: 'Home', icon: 'dashboard', to: '/' }

// The daily nav is a single flat list, top to bottom: Home, Ask, the Queries
// workspace, Benchmarks, Health Check, Schema, then the demo.
// Experimental surfaces (/scan, /agents, /guards) stay off the nav until they
// ship for real; their routes remain reachable by URL and keep their on-page
// Experimental banners.
const primaryItems: NavItem[] = [
  { label: 'Ask', icon: 'sparkles', to: '/ask' },
  { label: 'Queries', icon: 'folder-file', to: '/queries' },
  { label: 'Benchmarks', icon: 'speedometer', to: '/cache' },
  { label: 'Health check', icon: 'document-validation', to: '/audit' },
  { label: 'Schema', icon: 'layers', to: '/schema' },
  { label: 'Try the demo', icon: 'querypilot', to: '/demo' },
]

// Configuration recedes off the daily nav: after first connect the only global
// config control is the target switcher (top) plus a quiet footer "Settings"
// utility, never a top-level or Experimental nav item (configure-and-identity
// step 2 / T17). [USE-030, USE-034]
const settingsItem: NavItem = {
  label: 'Settings',
  icon: 'settings',
  to: '/configure',
}

function NavLink({
  item,
  active,
  running = false,
  size = 'nav',
  onNavigate,
}: {
  item: NavItem
  active: boolean
  running?: boolean
  size?: 'nav' | 'footer'
  onNavigate?: () => void
}) {
  return (
    <Link
      to={item.to}
      className={navItemStyles({ active, size })}
      onClick={() => {
        trackEvent('nav_item_clicked', { label: item.label })
        onNavigate?.()
      }}
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
          <ActivityPulse label={`${item.label} is running`} />
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
    // is the unified Benchmarks workspace.
    (item.to === '/cache' && currentPath === '/benchmark')

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

        {/* Target selector. */}
        <div className="border-b border-border-layout-1">
          <div className="draggable-region h-14">
            <div className="no-drag h-full">
              <TargetDropdown
                selectedTarget={selectedTarget}
                onSelectTarget={setSelectedTarget}
              />
            </div>
          </div>
        </div>

        {/* The one value-proposition line (C1 / D-5), sitting below the
          switcher rather than inside its bordered block: the sidebar is the
          single owner of "what is this app", not a caption on the current
          database (USE-050). */}
        <div className="no-drag px-3 pt-2 pb-3">
          <Text level="caption" className="text-content-layout-3">
            {VALUE_PROPOSITION}
          </Text>
        </div>

        {/* Navigation — persistent scrollbar keeps the full list reachable
          below the fold at 1280×720. [QW2] */}
        <Scrollable className="flex-1" type="auto">
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

            {/* Dev Settings is no longer a nav entry: its tools merged into
                the Settings page (Developer settings section, /configure#dev).
                The /dev-settings route now redirects there. [USE-097] */}
          </nav>
        </Scrollable>

        {/* Footer — status (who's signed in, what's running, trial credits,
          app update) and utilities (upsell + links) are separate groups: the
          gap between groups exceeds the gap within either one (F6, VIS-036). */}
        <div className="p-2 border-t border-border-layout-1">
          <div data-testid="sidebar-footer-status" className="space-y-1">
            <SidebarIdentity />
            <BackgroundRuns />
            <TrialBalanceBadge />
            {desktopUpdateState && (
              <div className="flex justify-end px-3 py-2">
                <DesktopUpdateControl
                  state={desktopUpdateState}
                  install={onInstallUpdate}
                />
              </div>
            )}
          </div>

          <div
            data-testid="sidebar-footer-utilities"
            className="mt-4 space-y-1"
          >
            {/* Quiet secondary control, not a competing focal point: no
              gradient/border/elevation, sized and weighted like the other
              footer utilities, with just the icon carrying a small accent
              color (F2, VIS-011/022/121). */}
            <Button
              type="button"
              label="Get free AI credits"
              icon="sparkles"
              iconPosition="left"
              modifier="outline"
              fullWidth
              onClick={() => setTrialOpen(true)}
              classMerge={navItemStyles({
                size: 'footer',
                className:
                  'cursor-pointer border-0 [&_svg]:text-content-primary-soft',
              })}
            />
            {/* Settings recedes here as a quiet utility, out of the daily nav. */}
            <NavLink
              item={settingsItem}
              active={isActive(settingsItem)}
              size="footer"
            />
            {/* Docs — kept as a hand-roll: it reuses navItemStyles so it reads as
                a sibling of the NavLinks above while opening the docs site in a
                new tab rather than navigating in-app (C4). No help-center
                build-out here, just the one reachable link. Icon label is empty
                (decorative) so the composed accessible name reads just "Docs",
                not a duplicate of the visible text. */}
            <a
              href="https://readyset.io/docs"
              target="_blank"
              rel="noreferrer"
              onClick={() => trackEvent('nav_item_clicked', { label: 'Docs' })}
              className={navItemStyles({
                size: 'footer',
                className: 'cursor-pointer',
              })}
            >
              <Icon
                name="info"
                label=""
                aria-hidden="true"
                className="w-4 h-4 text-content-layout-3 group-hover:scale-110 transition-transform"
              />
              <span>Docs</span>
            </a>
            {/* The dismissed setup guide's only way back (D). Absent unless it
                was dismissed with steps still outstanding, so a finished install
                never carries a dead utility. */}
            <SetupGuideHelpEntry
              className={navItemStyles({
                size: 'footer',
                className: 'cursor-pointer',
              })}
            />
            {/* Give Feedback — kept as a hand-roll: it reuses navItemStyles so it
              reads as a sibling of the NavLinks above while opening a dialog
              rather than navigating; a ui-new Button would break that shared
              nav-item styling. */}
            <Pressable
              type="button"
              onClick={() => setReportOpen(true)}
              className={navItemStyles({
                size: 'footer',
                className: 'cursor-pointer',
              })}
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

            {status?.version && (
              <div className="px-3 py-2" title={`v${status.version}`}>
                <Text
                  level="caption"
                  className="truncate text-content-layout-3"
                >
                  v{status.version}
                </Text>
              </div>
            )}
          </div>
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
