// Database page header (DESIGN_V4 section 1): identity and one live pulse. The glyph
// heartbeat in its countdown ring is the only "is RDST watching" element; hover it
// for when the next reading lands. Below: the Actions, Indexes, Queries and Reports tabs, with
// the range control (Queries) or the report picker (Reports) docked at the right.
import { Button } from '@rs/ui-new/button'
import { IconButton } from '@rs/ui-new/icon-button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState } from 'react'
import {
  AllowanceNotice,
  MonitoringStatusAction,
} from '@/features/monitor-kit/AiMonitoringStatus'
import {
  monitoringStatus,
  needsUserAction,
} from '@/features/monitor-kit/aiMonitoring'
import type { GlyphTone } from '@/features/monitor-kit/DatabaseGlyph'
import { ReadingActions } from '@/features/monitor-kit/MonitorHeartbeat'
import {
  analysisSummary,
  MonitorWorkList,
} from '@/features/monitor-kit/MonitorWorkList'
import { PulseHalo } from '@/features/monitor-kit/PulseHalo'
import type { MonitorPulseState } from '@/features/monitor-kit/useMonitorPulse'
import { fetchAccountStatus } from '@/lib/api'
import { useMonitoringLimit, useMonitorWork } from '@/lib/monitor/hooks'
import type { MonitorHealth, MonitorSettings } from '@/lib/monitor/types'
import { MonitoringMenu } from '../settings/MonitoringSwitch'
import type { DatabaseView } from '../useDatabaseUrl'

const ENGINE_LABEL: Record<MonitorHealth['engine'], string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
}

/**
 * True while the live pulse reports AI paused and the account really is signed
 * out. A stale server state never shows the alert to a signed-in user, and signing
 * in (which refreshes account-status) hides it at once.
 */
export function useSignInNeeded(state: MonitorPulseState['state']): boolean {
  const account = useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
    staleTime: 60_000,
  })
  return state === 'ai_paused' && account.data?.signed_in === false
}

export interface DatabaseHeaderProps {
  target: string
  health: MonitorHealth | undefined
  settings: MonitorSettings | undefined
  pulse: MonitorPulseState
  glyphTone: GlyphTone
  view: DatabaseView
  onViewChange: (view: DatabaseView) => void
  /** Docked right of the tabs: the range control or the report picker. */
  toolbar?: ReactNode
  /** A slim line between the tabs and the toolbar (the latest report, a run). */
  teaser?: ReactNode
  /** A report is waiting that the user hasn't opened. */
  reportDot?: boolean
  /** Open actions, shown on the Actions tab. */
  openActions?: number
  /** Something is happening on Actions: a checkup running, or a plan not yet opened. */
  actionsLive?: boolean
  /** Deep analyses are running or queued for this database. */
  queriesLive?: boolean
  /** Picks another database; docked with the header's controls. */
  switcher?: ReactNode
  signInNeeded?: boolean
  onOpenSettings: () => void
  onEnable: () => void
  onCheckAgain: () => void
}

export function DatabaseHeader({
  target,
  health,
  settings,
  pulse,
  glyphTone,
  view,
  onViewChange,
  toolbar,
  teaser,
  reportDot = false,
  openActions = 0,
  actionsLive = false,
  queriesLive = false,
  switcher,
  signInNeeded = false,
  onOpenSettings,
  onEnable,
  onCheckAgain,
}: DatabaseHeaderProps) {
  const enabled = settings?.enabled ?? true
  const state = enabled
    ? pulse.firstReading
      ? 'unknown'
      : pulse.state
    : 'paused'
  // "Starting" turns into a reason after a minute, without waiting for a refetch.
  const [now, setNow] = useState(() => Date.now() / 1000)
  const pending = enabled && settings?.ai_monitoring === 'pending'
  useEffect(() => {
    if (!pending) return
    const id = window.setInterval(() => setNow(Date.now() / 1000), 5_000)
    return () => window.clearInterval(id)
  }, [pending])
  const status = monitoringStatus({
    enabled: settings ? settings.enabled : undefined,
    ai: settings?.ai_monitoring,
    reason: signInNeeded ? 'signed_out' : settings?.ai_reason,
    since: settings?.ai_since,
    connectionLost: state === 'connection_lost',
    connectionProblem: pulse.connectionProblem,
    now,
  })
  const limit = useMonitoringLimit(undefined, enabled && Boolean(settings))
  const engine = health
    ? `${ENGINE_LABEL[health.engine]}${health.engine_version ? ` ${health.engine_version}` : ''}`
    : null
  const fill =
    health?.now.active_conns != null && health.now.max_conns
      ? health.now.active_conns / health.now.max_conns
      : null
  // Off and paused read from the monitoring status; only a late reading adds a word.
  const stateWord =
    enabled && pulse.firstReading
      ? 'Taking the first reading'
      : state === 'late'
        ? `Readings delayed${pulse.delayReason ? `. ${pulse.delayReason}` : ''}`
        : null
  const actionNeeded = Boolean(settings) && needsUserAction(status)

  return (
    <header className="space-y-4">
      {enabled && pulse.target && <ReadingActions pulse={pulse} />}
      <div className="flex flex-col gap-4 tablet:flex-row tablet:items-center tablet:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <PulseHalo
            pulse={pulse}
            monitoringEnabled={enabled}
            startup={
              settings?.enabled_at != null &&
              pulse.now - settings.enabled_at < 300
            }
            qps={health ? health.now.qps : null}
            fill={fill}
            tone={glyphTone}
          />
          <div className="min-w-0 space-y-1">
            <Text
              as="h1"
              level="headline-3"
              className="truncate text-content-layout-1"
            >
              {target}
            </Text>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-label-medium">
              {engine ? (
                <span className="text-content-layout-2">{engine}</span>
              ) : (
                <Skeleton className="h-4 w-28" />
              )}
              {stateWord && (
                <>
                  <span aria-hidden="true" className="text-content-layout-3">
                    ·
                  </span>
                  <span className="text-content-warning-soft">{stateWord}</span>
                </>
              )}
              {settings ? (
                <>
                  <span aria-hidden="true" className="text-content-layout-3">
                    ·
                  </span>
                  <MonitoringMenu
                    target={target}
                    settings={settings}
                    status={status}
                    live={
                      enabled && (state === 'live' || state === 'ai_paused')
                    }
                    onEnable={onEnable}
                    onOpenSettings={onOpenSettings}
                  />
                </>
              ) : null}
              {enabled && settings && status.kind !== 'off' ? (
                <DeepAnalysisStatus target={target} />
              ) : null}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {actionNeeded && (
            <MonitoringStatusAction
              target={target}
              status={status}
              contact={limit.data?.contact}
            />
          )}
          {state === 'connection_lost' && (
            <Button
              label="Check again"
              modifier="outline"
              size="small"
              onClick={onCheckAgain}
            />
          )}
          {enabled ? null : (
            <Button label="Turn on monitoring" onClick={onEnable} />
          )}
          {switcher}
          <IconButton
            icon="settings"
            label="Monitoring settings"
            modifier="ghost"
            onClick={onOpenSettings}
          />
        </div>
      </div>

      {state === 'connection_lost' && pulse.lastCollect?.ok === false && (
        <Text as="p" level="body-small" className="text-content-negative-soft">
          {pulse.lastCollect.detail}
        </Text>
      )}

      {status.kind !== 'local_only' && enabled ? (
        <AllowanceNotice limit={limit.data} />
      ) : null}

      <div className="flex flex-col gap-3 border-b border-border-layout-1 tablet:flex-row tablet:items-end tablet:justify-between tablet:gap-4">
        <TabList
          aria-label="Database view"
          className="-mb-px w-auto gap-6 border-b-0"
        >
          <TabItemButton
            layoutPrefix="database-view"
            label={openActions ? `Actions ${openActions}` : 'Actions'}
            active={view === 'actions'}
            onClick={() => onViewChange('actions')}
          />
          {actionsLive ? <LiveDot label="Actions are on their way" /> : null}
          <TabItemButton
            layoutPrefix="database-view"
            label="Queries"
            active={view === 'live'}
            onClick={() => onViewChange('live')}
          />
          {queriesLive ? <LiveDot label="Analyses running" /> : null}
          <TabItemButton
            layoutPrefix="database-view"
            label="Reports"
            active={view === 'report'}
            onClick={() => onViewChange('report')}
          />
          {reportDot && view !== 'report' ? (
            <span
              role="img"
              aria-label="New report"
              className="-ml-5 mt-3 h-2 w-2 self-start rounded-full bg-surface-rising-solid"
            />
          ) : null}
        </TabList>
        {teaser ? (
          <div className="hidden min-w-0 flex-1 justify-end pb-2 laptop:flex">
            {teaser}
          </div>
        ) : null}
        {toolbar ? (
          <div className="flex min-w-0 shrink-0 items-center gap-2 pb-2">
            {toolbar}
          </div>
        ) : null}
      </div>
    </header>
  )
}

/** A breathing dot beside a tab: something there is in progress or new. */
function LiveDot({ label }: { label: string }) {
  return (
    <span
      role="img"
      aria-label={label}
      data-testid="tab-live-dot"
      className="-ml-5 mt-3 h-2 w-2 self-start rounded-full bg-content-rising-plain mk-breathe"
    />
  )
}

/**
 * "Deep analysis: 1 running, 4 queued": this database's slice of the app's Analyses list,
 * always shown while monitoring is on. It opens the same list, filtered. Daily limits
 * stay out of sight until one is hit.
 */
function DeepAnalysisStatus({ target }: { target: string }) {
  const [open, setOpen] = useState(false)
  const work = useMonitorWork().data
  const running = work?.running.some((job) => job.target === target) ?? false
  const queued = work?.queued.some((job) => job.target === target) ?? false
  if (!running && !queued && !open) return null
  return (
    <>
      <span aria-hidden="true" className="text-content-layout-3">
        ·
      </span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Pressable
            type="button"
            data-testid="deep-analysis-status"
            className="inline-flex items-center gap-1.5 rounded-control text-label-medium text-content-layout-2 underline decoration-dotted underline-offset-4 hover:text-content-layout-1"
          >
            {running ? (
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full bg-content-rising-plain mk-breathe"
              />
            ) : null}
            Deep analysis: {analysisSummary(work, target).toLowerCase()}
          </Pressable>
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          className="w-80 max-w-[calc(100vw-1.5rem)] flex-col p-0"
          aria-label={`Deep analysis on ${target}`}
        >
          <div className="border-b border-border-layout-1 px-4 py-3">
            <Text level="label-small" className="text-content-layout-1">
              Deep analysis on {target}
            </Text>
          </div>
          <MonitorWorkList
            target={target}
            defaultOpen
            onOpened={() => setOpen(false)}
          />
        </PopoverContent>
      </Popover>
    </>
  )
}
