import { cn } from '@rs/tailwind-base'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Icon } from '@rs/ui-new/icon'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useEffect, useState } from 'react'
import { getDesktopSystemNotifications, isDesktopRuntime } from '@/lib/desktop'
import {
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import type { MuteRule, NotifyEvents } from '@/lib/monitor/types'
import { useDesktopNotificationHealth } from '@/lib/useDesktopNotificationHealth'
import {
  desktopNotificationSupport,
  desktopNotificationsEnabled,
  onDesktopNotificationsChange,
  setDesktopNotifications,
} from './desktopNotifications'
import { MuteRulesList } from './MuteRulesList'

const PUSH_LIMITS = [0, 1, 3, 5, 10] as const

/** The events that can notify, in the order people care about them. */
export const NOTIFY_EVENT_ROWS: {
  key: keyof NotifyEvents
  name: string
  text: string
}[] = [
  {
    key: 'database_unreachable',
    name: 'Database unreachable',
    text: "RDST can't connect, so monitoring is paused. Never held back by the daily limit.",
  },
  {
    key: 'database_recovered',
    name: 'Monitoring resumed',
    text: 'RDST reached the database again after it was unreachable.',
  },
  {
    key: 'stuck_query',
    name: 'Stuck query',
    text: 'A query has been running for over a minute.',
  },
  {
    key: 'query_changes',
    name: 'Query changes',
    text: 'A query that carries real load slows down or changes plan, or a new heavy query shows up.',
  },
  {
    key: 'database_events',
    name: 'Database-wide changes',
    text: 'Connections approach their limit, rollbacks or deadlocks spike, or fewer reads come from memory.',
  },
  {
    key: 'monitoring_setup',
    name: 'Monitoring needs you',
    text: 'Sign-in expired, the database password is missing, or setup is incomplete.',
  },
]

export const NOTIFY_EVENT_DEFAULTS: NotifyEvents = {
  database_unreachable: true,
  database_recovered: false,
  monitoring_setup: true,
  stuck_query: true,
  database_events: true,
  query_changes: true,
}

function Row({
  id,
  label,
  text,
  checked,
  disabled,
  onChange,
  children,
}: {
  id: string
  label: string
  text: ReactNode
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
  children?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={id} className="text-subtitle-2 text-content-layout-1">
          {label}
        </label>
        <Text level="caption" className="mt-1 block text-content-layout-3">
          {text}
        </Text>
        {children}
      </div>
      <BaseInputSwitch
        id={id}
        name={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
      />
    </div>
  )
}

/** The desktop app's system notifications: the primary channel, switchable in the app. */
function SystemNotifications() {
  const [bridge] = useState(getDesktopSystemNotifications)
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!bridge) return
    let live = true
    void bridge
      .get()
      .then((value) => live && setEnabled(value))
      .catch(() => live && setEnabled(null))
    return () => {
      live = false
    }
  }, [bridge])
  if (!bridge)
    return (
      <div className="border-b border-border-layout-1 p-5">
        <Text as="p" level="subtitle-2" className="text-content-layout-1">
          System notifications
        </Text>
        <Text level="caption" className="mt-1 block text-content-layout-3">
          The RDST desktop app shows system notifications on Windows, macOS and
          Linux, even when its window is closed, and keeps monitoring from the
          system tray. In a browser, use Browser notifications below.
        </Text>
      </div>
    )
  return (
    <div className="border-b border-border-layout-1 p-5">
      <Row
        id="inbox-system-notifications"
        label="System notifications"
        text="Pop up on this computer even when the RDST window is closed. Same switch as Notifications in the tray menu."
        checked={enabled ?? false}
        disabled={enabled == null || busy}
        onChange={(next) => {
          setBusy(true)
          void bridge
            .set(next)
            .then(setEnabled)
            .finally(() => setBusy(false))
        }}
      />
    </div>
  )
}

/** A browser tab's own notifications: the only channel outside the desktop app. */
function BrowserNotifications() {
  const [enabled, setEnabled] = useState(desktopNotificationsEnabled)
  const [busy, setBusy] = useState(false)
  useEffect(
    () =>
      onDesktopNotificationsChange(() =>
        setEnabled(desktopNotificationsEnabled())
      ),
    []
  )
  const support = desktopNotificationSupport()
  const reason =
    support === 'unsupported'
      ? "This browser doesn't support notifications."
      : support === 'denied'
        ? 'Notifications are blocked for this site. Allow them in your browser settings, then turn this on.'
        : 'Pop up on this computer only while an RDST tab is open in this browser, even in the background.'
  return (
    <div className="border-b border-border-layout-1 p-5">
      <Row
        id="inbox-desktop-notifications"
        label="Browser notifications"
        text={reason}
        checked={enabled}
        disabled={support !== 'ok' || busy}
        onChange={(next) => {
          setBusy(true)
          void setDesktopNotifications(next)
            .then(setEnabled)
            .finally(() => setBusy(false))
        }}
      />
    </div>
  )
}

/** Which events notify on one database; switched-off events still land in the Inbox. */
function NotifyWhen({ target }: { target: string }) {
  const settings = useMonitorSettings(target)
  const update = useUpdateMonitorSettings(target)
  const events = { ...NOTIFY_EVENT_DEFAULTS, ...settings.data?.notify_events }
  return (
    <div
      className="space-y-3 border-b border-border-layout-1 p-5"
      data-testid="notify-when"
    >
      <div>
        <Text as="p" level="subtitle-2" className="text-content-layout-1">
          Notify me when, on {target}
        </Text>
        <Text level="caption" className="mt-1 block text-content-layout-3">
          Everything still lands in the Inbox. These choose what also pops up.
        </Text>
      </div>
      <ul className="space-y-3">
        {NOTIFY_EVENT_ROWS.map((row) => (
          <li key={row.key}>
            <Row
              id={`notify-event-${row.key}`}
              label={row.name}
              text={row.text}
              checked={events[row.key]}
              disabled={!settings.data || update.isPending}
              onChange={(next) =>
                update.mutate({ notify_events: { [row.key]: next } })
              }
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Notifications a day for one database; the rest wait in the Inbox and the daily report. */
function PushLimit({ target }: { target: string }) {
  const settings = useMonitorSettings(target)
  const update = useUpdateMonitorSettings(target)
  const value = settings.data?.push_daily_cap ?? 3
  return (
    <div className="space-y-3 border-b border-border-layout-1 p-5">
      <div>
        <Text as="p" level="subtitle-2" className="text-content-layout-1">
          Notifications a day on {target}
        </Text>
        <Text level="caption" className="mt-1 block text-content-layout-3">
          Past this limit, changes still land in the Inbox and in the daily
          report. A database that can't be reached always notifies and doesn't
          count toward it.
        </Text>
      </div>
      <SegmentedControl<string>
        aria-label={`Notifications a day on ${target}`}
        value={String(value)}
        onValueChange={(next) =>
          update.mutate({ push_daily_cap: Number(next) })
        }
        segments={PUSH_LIMITS.map((limit) => ({
          value: String(limit),
          label: limit === 0 ? 'None' : String(limit),
        }))}
      />
    </div>
  )
}

/** Where alerts reach people today, stated plainly, including what isn't built. */
function Channels({ desktop }: { desktop: boolean }) {
  const health = useDesktopNotificationHealth()
  const [browserSupport, setBrowserSupport] = useState(
    desktopNotificationSupport
  )
  useEffect(() => {
    const refresh = () => setBrowserSupport(desktopNotificationSupport())
    const unsubscribe = onDesktopNotificationsChange(refresh)
    window.addEventListener('focus', refresh)
    return () => {
      unsubscribe()
      window.removeEventListener('focus', refresh)
    }
  }, [])
  const channels = [
    { on: true, name: 'Inbox', text: 'Every alert lands here.' },
    {
      on: desktop && health?.blocked === false,
      name: 'System notifications',
      text: desktop
        ? (health?.message ??
          (health
            ? 'This app shows them even when its window is closed.'
            : 'Checking system notification availability.'))
        : 'From the RDST desktop app, even when its window is closed.',
    },
    ...(desktop
      ? []
      : [
          {
            on: browserSupport === 'ok',
            name: 'This browser',
            text: 'Only while an RDST tab is open.',
          },
        ]),
    {
      on: false,
      name: 'Email and phone',
      text: "Not available yet. RDST doesn't send alerts to email or to your phone.",
    },
  ]
  return (
    <div className="space-y-3 border-b border-border-layout-1 p-5">
      <Text as="p" level="subtitle-2" className="text-content-layout-1">
        Where alerts go today
      </Text>
      <ul className="space-y-2.5">
        {channels.map((channel) => (
          <li key={channel.name} className="flex gap-2.5">
            <Icon
              name={channel.on ? 'tick' : 'close'}
              label={channel.on ? 'Available' : 'Not available'}
              className={cn(
                'mt-0.5 h-4 w-4 shrink-0',
                channel.on
                  ? 'text-content-positive-soft'
                  : 'text-content-layout-3'
              )}
            />
            <Text level="body-small" className="text-content-layout-2">
              <span className="font-medium text-content-layout-1">
                {channel.name}.
              </span>{' '}
              {channel.text}
            </Text>
          </li>
        ))}
      </ul>
    </div>
  )
}

interface NotificationSettingsDialogProps {
  open: boolean
  onClose: () => void
  /** The database whose events and push limit this dialog sets. */
  target?: string
  rules: MuteRule[] | undefined
  loading: boolean
  names: Map<string, string>
}

export function NotificationSettingsDialog({
  open,
  onClose,
  target,
  rules,
  loading,
  names,
}: NotificationSettingsDialogProps) {
  const desktop = isDesktopRuntime()
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContentContainer open={open}>
        <ModalContent size="base" className="overflow-hidden p-0">
          <div className="border-b border-border-layout-1 p-5">
            <ModalTitle className="h-auto">Notification settings</ModalTitle>
            <ModalDescription className="mt-1">
              Every alert lands in the Inbox. Urgent ones can also pop up on
              this computer. Muted alerts are still recorded, but they stay out
              of the Inbox and never notify.
            </ModalDescription>
          </div>
          <div className="max-h-[70vh] overflow-y-auto">
            <SystemNotifications />
            {!desktop && <BrowserNotifications />}
            {target && <NotifyWhen target={target} />}
            {target && <PushLimit target={target} />}
            <Channels desktop={desktop} />
            <div className="px-5 pt-4 pb-1">
              <Text level="subtitle-2" className="text-content-layout-1">
                Mute rules
              </Text>
            </div>
            <div className="pb-2">
              <MuteRulesList rules={rules} loading={loading} names={names} />
            </div>
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}
