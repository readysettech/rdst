import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { limitLine } from '../features/monitor-kit/aiMonitoring'
import { useMonitorPulse } from '../features/monitor-kit/useMonitorPulse'
import { useTarget } from '../hooks/useTarget'
import type { TargetInfo } from '../lib/api'
import { useMonitoringLimit, useMonitorSettings } from '../lib/monitor/hooks'
import { useTargetSwitchLockState } from '../lib/targetSwitchLock'
import { useSystemStatus } from '../lib/useSystemStatus'
import {
  TargetMonitorStatus,
  useTargetMonitorLines,
} from './TargetMonitorStatus'

/** Database page params that belong to one target: a new target starts without them. */
const TARGET_SCOPED_PARAMS = [
  'query',
  'tab',
  'zoom',
  'from',
  'queries',
  'report',
  'panel',
  'q',
  'table',
]

/** Pages that aren't about one database: picking a target there opens its Database page. */
const NON_DATABASE_PATHS = [
  '/',
  '/inbox',
  '/configure',
  '/fleet',
  '/agents',
  '/agent-activity',
]

/**
 * Where a target pick goes: `open` opens the Database page fresh (from a page that isn't
 * per-database), `stay` keeps the Database page with the target's own params dropped, and
 * null stays put (any other per-database page follows the new target in place).
 */
export function switchDestination(
  pathname: string,
  sameTarget: boolean
): 'open' | 'stay' | null {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (NON_DATABASE_PATHS.includes(path)) return 'open'
  if (sameTarget || path !== '/database') return null
  return 'stay'
}

interface TargetDropdownProps {
  selectedTarget: string | null
  onSelectTarget: (target: string | null) => void
  /** `compact` is an inline trigger for a page header; the default fills a sidebar row. */
  variant?: 'sidebar' | 'compact'
}

/**
 * The status read and the selected target it resolves to. A selection the target list
 * doesn't hold falls back to the default target, once the list is known to be current.
 */
export function useTargetSelection(
  selectedTarget: string | null,
  onSelectTarget: (target: string | null) => void
) {
  const {
    data: status,
    isLoading,
    error,
    refetch,
    dataUpdatedAt,
  } = useSystemStatus()

  const targets = status?.targets || []
  const normalizedSelectedTarget = selectedTarget?.trim() || null
  const isSelectedValid = normalizedSelectedTarget
    ? targets.some((target) => target.name === normalizedSelectedTarget)
    : false
  const fallbackTarget =
    status?.default_target &&
    targets.some((target) => target.name === status.default_target)
      ? status.default_target
      : targets.length > 0
        ? targets[0].name
        : null
  const currentTarget = isSelectedValid
    ? normalizedSelectedTarget
    : fallbackTarget
  // Track the last invalid→fallback correction we applied so status refetches
  // do not loop, while still allowing new invalid parent selections to resync.
  const lastCorrectionRef = useRef<string | null>(null)
  // The selection is shared by every open tab. A name this tab's list doesn't hold may be
  // a database another tab just added, so the list is read again before the selection is
  // overridden. Overriding it from a stale list made two tabs flip the selection back and
  // forth without end, remounting every page that follows the target.
  const verifyRef = useRef<{ name: string; after: number } | null>(null)
  const correctionKey =
    !isLoading && status && currentTarget !== normalizedSelectedTarget
      ? `${normalizedSelectedTarget ?? '<none>'}=>${currentTarget ?? '<none>'}`
      : null

  useEffect(() => {
    if (!correctionKey) {
      lastCorrectionRef.current = null
      verifyRef.current = null
      return
    }

    if (lastCorrectionRef.current === correctionKey) {
      return
    }

    if (normalizedSelectedTarget) {
      const verify = verifyRef.current
      if (verify?.name !== normalizedSelectedTarget) {
        verifyRef.current = {
          name: normalizedSelectedTarget,
          after: dataUpdatedAt,
        }
        void refetch()
        return
      }
      if (dataUpdatedAt <= verify.after) return
    }

    lastCorrectionRef.current = correctionKey
    onSelectTarget(currentTarget)
  }, [
    correctionKey,
    currentTarget,
    normalizedSelectedTarget,
    onSelectTarget,
    refetch,
    dataUpdatedAt,
  ])

  return { status, isLoading, error, targets, currentTarget }
}

/**
 * Keeps the global target valid and the selected target's monitor stream open across
 * page changes, so arriving on a page never restarts readings. Renders nothing.
 */
export function TargetSync() {
  const { target, setTarget } = useTarget()
  const { currentTarget } = useTargetSelection(target, setTarget)
  const settings = useMonitorSettings(currentTarget ?? '')
  useMonitorPulse(settings.data?.enabled ? currentTarget : null)
  return null
}

export function TargetDropdown({
  selectedTarget,
  onSelectTarget,
  variant = 'sidebar',
}: TargetDropdownProps) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const { isLocked, message: lockMessage } = useTargetSwitchLockState()
  const { status, isLoading, error, targets, currentTarget } =
    useTargetSelection(selectedTarget, onSelectTarget)
  const compact = variant === 'compact'
  const settings = useMonitorSettings(currentTarget ?? '')
  const pulse = useMonitorPulse(settings.data?.enabled ? currentTarget : null)
  const lines = useTargetMonitorLines(
    targets.map((target) => target.name),
    open,
    { target: currentTarget, state: pulse.state }
  )
  const limit = useMonitoringLimit(undefined, open)
  const summary = limitLine(limit.data)

  /** Switches target. A per-database page stays put, for the new target; any other
   * page (the Inbox, Home) opens the new target's Database page. */
  const switchTo = (name: string) => {
    onSelectTarget(name)
    const destination = switchDestination(
      window.location.pathname,
      name === currentTarget
    )
    if (destination === 'open') {
      void navigate({ to: '/database' })
      return
    }
    if (destination !== 'stay') return
    void navigate({
      to: '/database',
      search: ((previous: Record<string, unknown>) => {
        const next = { ...previous }
        for (const param of TARGET_SCOPED_PARAMS) delete next[param]
        return next
      }) as never,
      replace: true,
    })
  }

  useEffect(() => {
    if (isLocked) {
      setOpen(false)
    }
  }, [isLocked])

  // Lock still forces the menu visually closed immediately while the effect above
  // clears the internal open state so it stays closed after unlocking.
  const effectiveOpen = open && !isLocked

  // The compact trigger sits inline in a page header; the sidebar one fills its row.
  const frame = compact
    ? 'h-9 max-w-xs px-2.5 rounded-lg border border-border-layout-1'
    : 'p-2 pr-3 h-14 w-full rounded-lg'

  if (isLoading) {
    return compact ? (
      <Skeleton className="h-9 w-40 rounded-lg" />
    ) : (
      <div className="p-2 pr-3 h-14 w-full flex items-center gap-2">
        <Skeleton className="h-10 w-10 rounded-md" />
        <Skeleton className="h-4 w-24" />
      </div>
    )
  }

  if (error || !status) {
    return (
      <div className={`flex ${frame} items-center gap-2`}>
        <Icon
          name="alert"
          label="Error"
          className="text-content-negative-soft"
        />
        <Text level="label-medium" className="text-content-negative-soft">
          Connection Error
        </Text>
      </div>
    )
  }

  const currentTargetInfo = targets.find((t) => t.name === currentTarget)
  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen && isLocked) return
    setOpen(nextOpen)
  }

  // A real <Pressable> so the one always-visible config control is keyboard
  // focusable and Enter/Space-activatable — the Dropdown.Trigger→<Pressable> fix
  // (configure-and-identity step 2 / app-chrome HIGH). [USE-030, USE-018]
  const trigger = (
    <Pressable
      type="button"
      aria-label={`Switch target, current: ${currentTarget ?? 'none'}${
        currentTarget && lines.get(currentTarget)
          ? `, ${lines.get(currentTarget)?.label}`
          : ''
      }`}
      aria-disabled={isLocked}
      title={isLocked ? lockMessage : undefined}
      className={`flex justify-between items-center gap-2 text-content-layout-1 ${frame} ${
        isLocked
          ? 'cursor-not-allowed opacity-60 bg-surface-layout-2/50'
          : 'cursor-pointer hover:bg-surface-primary-soft-hover focus-visible:shadow-focus focus-visible:outline-none'
      }`}
    >
      <div className="flex min-w-0 gap-2 items-center">
        {compact ? (
          <Icon
            name="database"
            label=""
            aria-hidden="true"
            size="small"
            className="shrink-0 text-content-layout-3"
          />
        ) : (
          <div className="w-8 h-8 shrink-0 rounded-md bg-surface-layout-2 flex items-center justify-center">
            <Icon name="database" label="Database" size="small" />
          </div>
        )}
        <div className="flex min-w-0 flex-col items-start">
          <Text level="label-medium" className="max-w-full truncate">
            {currentTarget}
          </Text>
          {currentTarget && !compact ? (
            <TargetMonitorStatus
              line={lines.get(currentTarget)}
              compact
              className="max-w-full"
            />
          ) : null}
        </div>
      </div>
      <div className="flex gap-1 items-center">
        {currentTargetInfo && !currentTargetInfo.has_password && (
          <Icon
            name="alert"
            label="No password"
            size="small"
            className="text-content-warning-soft"
          />
        )}
        <Icon
          name="chevron-down"
          label="Target dropdown"
          size={compact ? 'small' : undefined}
          className={compact ? 'text-content-layout-3' : undefined}
        />
      </div>
    </Pressable>
  )

  if (targets.length === 0) {
    return (
      <div className={`flex ${frame} items-center gap-2`}>
        <Icon
          name="alert"
          label=""
          aria-hidden="true"
          className="text-content-warning-soft"
        />
        <Text level="label-medium" className="text-content-warning-soft">
          No targets
        </Text>
      </div>
    )
  }

  return (
    <Dropdown open={effectiveOpen} onOpenChange={handleOpenChange}>
      {isLocked ? (
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>{trigger}</TooltipTrigger>
            <TooltipContent label={lockMessage} />
          </Tooltip>
        </TooltipProvider>
      ) : (
        <Dropdown.Trigger asChild>{trigger}</Dropdown.Trigger>
      )}
      <Dropdown.Content align="start" className="min-w-72">
        {summary ? (
          <>
            <Text
              as="p"
              level="label-small"
              className="px-3 pt-2 pb-1 text-content-layout-2"
              data-testid="target-switcher-summary"
            >
              {summary}
            </Text>
            <Dropdown.Separator />
          </>
        ) : null}
        {targets.map((target: TargetInfo) => (
          <Dropdown.ItemWithChildren
            key={target.name}
            active={target.name === currentTarget}
            className="h-auto min-h-14 py-2"
            onClick={() => {
              if (isLocked) {
                return
              }
              switchTo(target.name)
              setOpen(false)
            }}
          >
            <div className="flex min-w-0 gap-2 items-center">
              <div className="w-8 h-8 shrink-0 rounded-md bg-surface-layout-2 flex items-center justify-center">
                <Icon name="database" label="Database" size="small" />
              </div>
              <div className="flex min-w-0 flex-col items-start">
                <span className="flex min-w-0 items-center gap-1">
                  <Text level="label-medium" className="truncate">
                    {target.name}
                  </Text>
                  {target.is_default && (
                    <Text level="label-small" className="text-content-layout-3">
                      (default)
                    </Text>
                  )}
                </span>
                <TargetMonitorStatus line={lines.get(target.name)} />
              </div>
            </div>
            {target.has_password ? (
              <Icon
                name="key"
                label="Password stored"
                className="text-content-layout-3"
              />
            ) : (
              <Icon
                name="alert"
                label="No password configured"
                className="text-content-warning-soft"
              />
            )}
          </Dropdown.ItemWithChildren>
        ))}
        {/* Switching is only half of what the reader came for: the popover also
            carries the way on to adding, editing and removing targets, so a
            single-target install has a next step. [A-25] */}
        <Dropdown.Separator />
        <Dropdown.Item
          label="Manage targets"
          leftIcon="settings"
          onClick={() => {
            setOpen(false)
            void navigate({
              to: '/configure',
              search: { panel: 'connections' },
            })
          }}
        />
      </Dropdown.Content>
    </Dropdown>
  )
}
