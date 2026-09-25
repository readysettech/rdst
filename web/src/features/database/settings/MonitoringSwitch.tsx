// The one on/off control for a database's monitoring, used in the database's settings
// and in Settings > Targets so both read and behave the same. Turning on opens the
// turn-on dialog (prerequisites, AI slot, deep analysis); turning off asks first. The
// Database page header carries the same actions as a menu on its monitoring state.
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { useEffect, useState } from 'react'
import {
  MonitoringStatusAction,
  MonitoringStatusBadge,
} from '@/features/monitor-kit/AiMonitoringStatus'
import {
  type MonitoringStatus,
  monitoringStatus,
  NO_AI_WARNING,
  needsUserAction,
} from '@/features/monitor-kit/aiMonitoring'
import {
  useMonitoringLimit,
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import type { MonitorSettings } from '@/lib/monitor/types'
import { EnableMonitoringDialog } from './EnableMonitoringDialog'

export const TURN_OFF_MESSAGE =
  'RDST stops reading this database. Charts keep the history recorded so far, and new changes go unnoticed until you turn it back on. This database stops using one of your AI monitoring spots.'

/** Asks before monitoring turns off, then turns it off. */
export function TurnOffMonitoringDialog({
  target,
  open,
  onClose,
}: {
  target: string
  open: boolean
  onClose: () => void
}) {
  const update = useUpdateMonitorSettings(target)
  const { toast } = useToast()
  return (
    <ConfirmDialog
      isOpen={open}
      onClose={onClose}
      onConfirm={() => {
        onClose()
        update.mutate(
          { enabled: false },
          {
            onSuccess: () =>
              toast({
                title: `Monitoring is off for ${target}`,
                variant: 'positive',
              }),
            onError: (error) =>
              toast({
                title: "Couldn't turn off monitoring",
                description: error instanceof Error ? error.message : undefined,
                variant: 'negative',
              }),
          }
        )
      }}
      title={`Turn off monitoring for ${target}?`}
      notice={{ accent: 'warning', icon: 'alert', message: TURN_OFF_MESSAGE }}
      confirmLabel="Turn off monitoring"
      confirmVariant="primary"
    />
  )
}

/**
 * "Monitoring" with the database's state and one switch. `withStatus` shows the shared
 * state badge ("AI monitoring on", "Local monitoring only (AI off: ...)", "Monitoring off").
 * `warning` shows the no-AI warning under it; a host with its own AI panel says it there.
 */
export function MonitoringSwitch({
  target,
  withStatus = true,
  warning = true,
}: {
  target: string
  withStatus?: boolean
  warning?: boolean
}) {
  const settings = useMonitorSettings(target)
  const [enableOpen, setEnableOpen] = useState(false)
  const [confirmOff, setConfirmOff] = useState(false)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const data = settings.data
  const pending = data?.enabled && data.ai_monitoring === 'pending'
  useEffect(() => {
    if (!pending) return
    const id = window.setInterval(() => setNow(Date.now() / 1000), 5_000)
    return () => window.clearInterval(id)
  }, [pending])
  const status = data
    ? monitoringStatus({
        enabled: data.enabled,
        ai: data.ai_monitoring,
        reason: data.ai_reason,
        since: data.ai_since,
        now,
      })
    : null
  const noAi = status ? needsUserAction(status) : false
  const limit = useMonitoringLimit(undefined, noAi)
  return (
    <div className="min-w-0 space-y-2">
      <div
        className="flex min-w-0 items-center gap-3"
        data-testid="monitoring-switch"
        data-target={target}
      >
        <BaseInputSwitch
          name={`monitoring-${target}`}
          checked={Boolean(data?.enabled)}
          disabled={!data}
          aria-label={`Monitoring for ${target}`}
          onCheckedChange={(checked) =>
            checked ? setEnableOpen(true) : setConfirmOff(true)
          }
        />
        {withStatus && status ? (
          <MonitoringStatusBadge
            status={status}
            withReason={status.kind === 'local_only'}
          />
        ) : null}
        <EnableMonitoringDialog
          target={target}
          open={enableOpen}
          onClose={() => setEnableOpen(false)}
        />
        <TurnOffMonitoringDialog
          target={target}
          open={confirmOff}
          onClose={() => setConfirmOff(false)}
        />
      </div>
      {warning && noAi && status ? (
        <output
          data-testid="no-ai-warning"
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-panel border border-border-warning-soft bg-surface-warning-soft px-3 py-2"
        >
          <Text
            as="span"
            level="body-small"
            className="min-w-0 flex-1 text-content-warning-soft"
          >
            {NO_AI_WARNING}
          </Text>
          <MonitoringStatusAction
            target={target}
            status={status}
            contact={limit.data?.contact}
          />
        </output>
      ) : null}
    </div>
  )
}

/**
 * The database monitoring menu turns monitoring on or off and opens its settings.
 */
export function MonitoringMenu({
  target,
  settings,
  status,
  withReason,
  live = false,
  onEnable,
  onOpenSettings,
}: {
  target: string
  settings: MonitorSettings
  status: MonitoringStatus
  withReason?: boolean
  /** RDST is collecting right now: the state dot beats. */
  live?: boolean
  onEnable: () => void
  onOpenSettings: () => void
}) {
  const [open, setOpen] = useState(false)
  const [confirmOff, setConfirmOff] = useState(false)
  const pick = (action: () => void) => () => {
    setOpen(false)
    action()
  }
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Pressable
            type="button"
            aria-label={`Monitoring for ${target}: ${status.label}`}
            data-testid="monitoring-menu-trigger"
            className="inline-flex min-w-0 items-center gap-1 rounded-control px-1 -mx-1 hover:bg-surface-layout-2 focus-visible:shadow-focus focus-visible:outline-none"
          >
            <MonitoringStatusBadge
              status={status}
              withReason={withReason}
              live={live}
            />
            <Icon
              name="chevron-down"
              label=""
              size="small"
              className="shrink-0 text-content-layout-3"
            />
          </Pressable>
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          className="w-72 max-w-[calc(100vw-1.5rem)] flex-col gap-0.5 p-1.5"
          aria-label={`Monitoring for ${target}`}
          data-testid="monitoring-menu"
        >
          <Text
            as="p"
            level="caption"
            className="px-2.5 pb-1.5 pt-1 text-content-layout-2"
            data-testid="monitoring-menu-detail"
          >
            {status.detail}
          </Text>
          {settings.enabled ? (
            <MenuItem
              label="Turn off monitoring"
              tone="negative"
              onClick={pick(() => setConfirmOff(true))}
            />
          ) : (
            <MenuItem label="Turn on monitoring" onClick={pick(onEnable)} />
          )}
          <MenuItem
            label="Monitoring settings"
            onClick={pick(onOpenSettings)}
          />
        </PopoverContent>
      </Popover>
      <TurnOffMonitoringDialog
        target={target}
        open={confirmOff}
        onClose={() => setConfirmOff(false)}
      />
    </>
  )
}

function MenuItem({
  label,
  detail,
  tone,
  onClick,
}: {
  label: string
  detail?: string
  tone?: 'negative'
  onClick: () => void
}) {
  return (
    <Pressable
      type="button"
      onClick={onClick}
      className="flex w-full flex-col items-start gap-0.5 rounded-control px-2.5 py-2 text-left hover:bg-surface-layout-2 focus-visible:shadow-focus focus-visible:outline-none"
    >
      <Text
        as="span"
        level="label-medium"
        className={
          tone === 'negative'
            ? 'text-content-negative-soft'
            : 'text-content-layout-1'
        }
      >
        {label}
      </Text>
      {detail ? (
        <Text as="span" level="caption" className="text-content-layout-3">
          {detail}
        </Text>
      ) : null}
    </Pressable>
  )
}
