import { cn, tv } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { focusRing } from '@rs/ui-new/focus'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Pressable } from '@rs/ui-new/pressable'
import { Scrollable } from '@rs/ui-new/scrollable'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useRouterState } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import {
  onDatabasesChanged,
  useOpenAddDatabase,
} from '../components/configure/addDatabase'
import { ACTIVE_RUN } from '../features/agent-activity/RunningNow'
import { useInboxUnread } from '../features/inbox/useInboxUnread'
import { RADAR_STATE, Radar, RadarTooltip } from '../features/monitor-kit/Radar'
import { useMonitorPulse } from '../features/monitor-kit/useMonitorPulse'
import { DATABASE_WORD } from '../features/shared/filters/DatabaseFilter'
import { useDefaultTarget } from '../hooks/useTarget'
import { trackEvent } from '../lib/analytics'
import {
  reattachBackgroundRuns,
  useBackgroundRuns,
} from '../lib/backgroundRuns'
import {
  type ConfiguredDatabase,
  configuredTargetsKey,
  useConfiguredDatabases,
  useMonitorSettings,
} from '../lib/monitor/hooks'
import '../features/monitor-kit/charts/monitorKit.css'
import { PlatformCard } from './PlatformCard'
import { SidebarAccountMenu } from './SidebarAccountMenu'

const navItemStyles = tv({
  base: [
    'flex items-center gap-2.5 rounded-control px-2.5 text-content-layout-2',
    // The focus ring is painted the instant the caret arrives, so box-shadow
    // stays out of the transition.
    'transition-[background-color,color] duration-150',
    'hover:bg-surface-layout-1 hover:text-content-layout-1',
    ...focusRing,
  ],
  variants: {
    active: {
      true: 'bg-surface-layout-1 text-content-layout-1',
    },
    group: {
      primary: 'py-1.75 text-body-small',
      secondary: 'py-1.5 text-caption',
    },
  },
  defaultVariants: { group: 'primary' },
})

interface NavItem {
  label: string
  icon: IconStrokeName
  to: string
}

const primaryItems: NavItem[] = [
  { label: 'Home', icon: 'dashboard', to: '/' },
  { label: 'Inbox', icon: 'notification', to: '/inbox' },
  { label: 'Actions', icon: 'tick-double', to: '/actions' },
  { label: 'Ask', icon: 'message-multiple', to: '/ask' },
]

const secondaryItems: NavItem[] = [
  { label: 'Activity', icon: 'observe', to: '/agent-activity' },
  { label: 'Settings', icon: 'settings', to: '/configure' },
  { label: 'Help', icon: 'info', to: '/help' },
]

/** Databases listed before the rest fold under "N more". */
export const SHOWN_DATABASES = 8

function Badge({ count, title }: { count: number; title: string }) {
  return (
    <span
      className="ml-auto inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-pill bg-surface-negative-solid px-1.5 text-label-extra-small tabular-nums text-(--neutral-white)"
      title={title}
    >
      {count}
    </span>
  )
}

function Count({
  children,
  title,
}: {
  children: React.ReactNode
  title?: string
}) {
  return (
    <span
      className="ml-auto shrink-0 text-label-extra-small tabular-nums text-content-layout-3"
      title={title}
    >
      {children}
    </span>
  )
}

/** A breathing dot: work under way that will land here (the first plan being built). */
function LiveDot({ label }: { label: string }) {
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-testid="nav-live-dot"
      className="h-1.5 w-1.5 shrink-0 rounded-full bg-content-rising-plain mk-breathe"
    />
  )
}

function NavLink({
  item,
  active,
  group = 'primary',
  badge,
  count,
  countTitle,
  live,
}: {
  item: NavItem
  active: boolean
  group?: 'primary' | 'secondary'
  /** Items that call for action now: Inbox items that need you, changes to do first. */
  badge?: { count: number; title: string }
  /** A quiet count: things running, changes to do. */
  count?: number | null
  countTitle?: string
  /** Names the live dot, shown while work for this page is under way. */
  live?: string | null
}) {
  return (
    <Link
      to={item.to}
      aria-current={active ? 'page' : undefined}
      className={navItemStyles({ active, group })}
      onClick={() => trackEvent('nav_item_clicked', { label: item.label })}
    >
      <Icon name={item.icon} label="" size="small" className="opacity-80" />
      <span>{item.label}</span>
      {live && <LiveDot label={live} />}
      {!!badge?.count && <Badge {...badge} />}
      {!badge?.count && !!count && <Count title={countTitle}>{count}</Count>}
    </Link>
  )
}

/** The right edge of a database row: its changes to do. The state lives in the tooltip. */
function databaseNote(db: ConfiguredDatabase): string | null {
  return db.toDo ? `${db.toDo} to do` : null
}

function DatabaseLink({
  db,
  active,
}: {
  db: ConfiguredDatabase
  active: boolean
}) {
  const word = db.state ? DATABASE_WORD[db.state] : null
  const note = databaseNote(db)
  const state = db.state ? RADAR_STATE[db.state] : 'off'
  return (
    <RadarTooltip
      state={state}
      activity={db.activity}
      name={db.name}
      toDo={db.toDo}
      side="right"
    >
      <Link
        to="/database"
        search={{ target: db.name } as never}
        aria-current={active ? 'page' : undefined}
        aria-label={[db.name, word, note].filter(Boolean).join(', ')}
        className={cn(
          navItemStyles({ active }),
          !db.monitored && !active && 'text-content-layout-3'
        )}
        onClick={() => trackEvent('nav_item_clicked', { label: 'Database' })}
      >
        <Radar
          state={state}
          activity={db.activity}
          name={db.name}
          tooltip={false}
        />
        <span className="min-w-0 truncate">{db.name}</span>
        {word && <span className="sr-only">{word}</span>}
        {note && <Count>{note}</Count>}
      </Link>
    </RadarTooltip>
  )
}

/** The configured databases: monitored first, more than eight folded under "N more". */
function DatabaseList({
  databases,
  activeName,
}: {
  databases: ConfiguredDatabase[]
  activeName: string | null
}) {
  const [unfolded, setUnfolded] = useState(false)
  const activeIndex = databases.findIndex((db) => db.name === activeName)
  const open = unfolded || activeIndex >= SHOWN_DATABASES
  const shown = open ? databases : databases.slice(0, SHOWN_DATABASES)
  const folded = databases.length - SHOWN_DATABASES
  return (
    <nav aria-label="Databases" className="flex flex-col gap-1">
      {shown.map((db) => (
        <DatabaseLink key={db.name} db={db} active={db.name === activeName} />
      ))}
      {folded > 0 && (
        <Pressable
          type="button"
          aria-expanded={open}
          className={cn(navItemStyles(), 'text-content-layout-3')}
          onClick={() => setUnfolded(!open)}
        >
          <Icon
            name={open ? 'chevron-up' : 'chevron-down'}
            label=""
            size="small"
            className="opacity-80"
          />
          <span>{open ? 'Fewer' : `${folded} more`}</span>
        </Pressable>
      )}
    </nav>
  )
}

/**
 * The sidebar on the page tone, flush with the canvas: the four pages that cover every
 * database, one item per configured database, the Readyset Platform card, then the
 * secondary group and the account line.
 */
export function Sidebar() {
  const currentPath = useRouterState({
    select: (state) => state.location.pathname,
  })
  // The Database page's selection: kept on a configured database, and its readings
  // stream held open here so arriving on the page never restarts them.
  const selected = useDefaultTarget()
  const selectedSettings = useMonitorSettings(selected ?? '')
  useMonitorPulse(selectedSettings.data?.enabled ? selected : null)

  const { databases, doFirst } = useConfiguredDatabases()
  // Actions counts the changes its page lists, in the alert tone while some are to do
  // first, and breathes while a database builds its first plan.
  const toDo =
    databases.reduce((sum, db) => sum + (db.toDo ?? 0), 0) || (doFirst ?? 0)
  const first = doFirst ?? 0
  const building = databases.some((db) => db.monitored && db.building)
  const inboxUnread = useInboxUnread()
  const openAddDatabase = useOpenAddDatabase()
  const queryClient = useQueryClient()
  // What Activity lists under Running now: this browser's runs, and each database's
  // analysis or report in progress.
  const running =
    useBackgroundRuns().filter((run) => ACTIVE_RUN.has(run.status)).length +
    databases.filter(
      (db) =>
        db.activity?.kind === 'analyzing' || db.activity?.kind === 'report'
    ).length
  // Runs this browser saved (a load test, a comparison) reload once per app start.
  useEffect(() => reattachBackgroundRuns(), [])
  useEffect(
    () =>
      onDatabasesChanged(() => {
        void queryClient.invalidateQueries({ queryKey: configuredTargetsKey })
        void queryClient.invalidateQueries({ queryKey: ['monitor', 'home'] })
      }),
    [queryClient]
  )

  const isActive = (item: NavItem) =>
    currentPath === item.to ||
    (item.to !== '/' && currentPath.startsWith(`${item.to}/`))
  const onDatabasePage =
    currentPath === '/database' || currentPath.startsWith('/database/')

  return (
    <aside
      id="app-sidebar"
      className="flex h-dvh w-58 shrink-0 flex-col gap-1 px-3.5 pb-4.5"
    >
      {/* The top drags the desktop window and holds the macOS traffic lights. */}
      <div className="draggable-region -mb-1 h-[max(--spacing(5.5),var(--titlebar-height))] shrink-0" />

      <nav aria-label="Primary" className="no-drag flex flex-col gap-1">
        {primaryItems.map((item) => (
          <NavLink
            key={item.to}
            item={item}
            active={isActive(item)}
            badge={
              item.to === '/inbox'
                ? {
                    count: inboxUnread.actNow,
                    title: `${inboxUnread.actNow} need you`,
                  }
                : item.to === '/actions' && first > 0
                  ? { count: toDo, title: `${toDo} to do, ${first} first` }
                  : undefined
            }
            count={item.to === '/actions' ? toDo : undefined}
            countTitle={item.to === '/actions' ? `${toDo} to do` : undefined}
            live={
              item.to === '/actions' && building
                ? 'Building your first plan'
                : null
            }
          />
        ))}
      </nav>

      <div className="mt-5 flex h-6 items-center pr-1 pl-2.5 text-caption text-content-layout-3">
        <span>Databases</span>
        <IconButton
          icon="add"
          label="Add database"
          variant="primary"
          modifier="ghost"
          size="small"
          classMerge="ml-auto h-5.5 w-5.5 min-w-5.5 text-content-layout-3"
          onClick={() => openAddDatabase()}
        />
      </div>
      <Scrollable rootClassName="min-h-0 flex-1">
        <DatabaseList
          databases={databases}
          activeName={onDatabasePage ? selected : null}
        />
      </Scrollable>

      <PlatformCard className="mt-auto mb-3" />

      <nav
        aria-label="Secondary"
        className="flex flex-col gap-1 border-t border-border-layout-soft pt-3.5"
      >
        {secondaryItems.map((item) => (
          <NavLink
            key={item.to}
            item={item}
            active={isActive(item)}
            group="secondary"
            count={item.to === '/agent-activity' ? running : undefined}
          />
        ))}
        <SidebarAccountMenu />
      </nav>
    </aside>
  )
}
