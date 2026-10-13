import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { IconButton } from '@rs/ui-new/icon-button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useState } from 'react'
import { thisComputer } from '@/lib/desktop'
import {
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import type { NotifyEvents, QuietHours } from '@/lib/monitor/types'
import {
  setDesktopNotifications,
  showDesktopNotification,
  useDesktopNotificationsOn,
} from './desktopNotifications'
import { MuteRulesDialog } from './MuteRulesList'

/** What pushes, in the order the Inbox files it. */
export const PUSH_ROWS: { key: keyof NotifyEvents; label: string }[] = [
  { key: 'stuck_query', label: 'Act now items' },
  { key: 'query_changes', label: 'Fixes ready' },
]

export const PUSH_DEFAULTS: NotifyEvents = {
  database_unreachable: true,
  database_recovered: false,
  monitoring_setup: true,
  stuck_query: true,
  database_events: true,
  query_changes: true,
  daily_report: true,
}

export const QUIET_HOURS = ['Off', '11 PM to 7 AM'] as const
export type QuietHoursChoice = (typeof QUIET_HOURS)[number]
const QUIET_WINDOW: QuietHours = { from_hour: 23, to_hour: 7 }
const QUIET_OFF: QuietHours = { from_hour: 0, to_hour: 0 }

/** The menu word for the database's quiet hours; equal hours mean off. */
export function quietHoursChoice(
  hours: QuietHours | undefined
): QuietHoursChoice {
  if (!hours) return '11 PM to 7 AM'
  return hours.from_hour === hours.to_hour ? 'Off' : '11 PM to 7 AM'
}

export function quietHoursFor(choice: QuietHoursChoice): QuietHours {
  return choice === 'Off' ? QUIET_OFF : QUIET_WINDOW
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center gap-3 border-b border-border-layout-2 px-2.5 text-body-small text-content-layout-1 last:border-b-0">
      <span className="flex-1">{label}</span>
      {children}
    </div>
  )
}

function SwitchRow({
  id,
  label,
  checked,
  disabled,
  onChange,
}: {
  id: string
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <div className="flex min-h-11 items-center gap-3 border-b border-border-layout-2 px-2.5 text-body-small text-content-layout-1">
      <label htmlFor={id} className="flex-1">
        {label}
      </label>
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

/** The database's push switches, the daily report switch and its quiet hours. */
function Controls({ target }: { target: string }) {
  const settings = useMonitorSettings(target)
  const update = useUpdateMonitorSettings(target)
  // Nothing pushes while Push to this computer is off, so no switch reads on.
  const push = useDesktopNotificationsOn()
  const events: NotifyEvents = {
    ...PUSH_DEFAULTS,
    ...settings.data?.notify_events,
  }
  const quiet = quietHoursChoice(settings.data?.quiet_hours)
  const disabled = !settings.data || update.isPending
  return (
    <>
      {PUSH_ROWS.map((row) => (
        <SwitchRow
          key={row.key}
          id={`notify-${row.key}`}
          label={row.label}
          checked={push && (events[row.key] ?? true)}
          disabled={disabled || !push}
          onChange={(next) =>
            update.mutate({ notify_events: { [row.key]: next } })
          }
        />
      ))}
      <SwitchRow
        id="notify-daily-report"
        label="Daily report"
        checked={push && (events.daily_report ?? true)}
        disabled={disabled || !push}
        onChange={(next) =>
          update.mutate({ notify_events: { daily_report: next } })
        }
      />
      <Text
        as="p"
        level="caption"
        className="px-2.5 py-2.5 text-content-layout-3"
      >
        {push
          ? 'Pushed once as a desktop notification. Nothing is sent by email.'
          : `Push to ${thisComputer()} is off, so nothing is pushed. Nothing is sent by email.`}
      </Text>
      <div className="my-1 border-t border-border-layout-2" />
      <Row label="Quiet hours">
        <Dropdown>
          <Dropdown.Trigger asChild>
            <Button
              label={quiet}
              icon="chevron-down"
              iconPosition="right"
              modifier="ghost"
              size="small"
              disabled={disabled}
              aria-label={`Quiet hours: ${quiet}`}
            />
          </Dropdown.Trigger>
          <Dropdown.Content align="end" className="min-w-48">
            {QUIET_HOURS.map((option) => (
              <Dropdown.Item
                key={option}
                label={option}
                active={option === quiet}
                rightIcon={option === quiet ? 'tick' : undefined}
                onSelect={() =>
                  update.mutate({ quiet_hours: quietHoursFor(option) })
                }
              />
            ))}
          </Dropdown.Content>
        </Dropdown>
      </Row>
    </>
  )
}

/** The bell: what pushes to the desktop, the quiet hours, and the mute rules. */
export function NotificationSettings({ target }: { target?: string }) {
  const [open, setOpen] = useState(false)
  const [rulesOpen, setRulesOpen] = useState(false)
  const push = useDesktopNotificationsOn()
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <IconButton
            icon="notification"
            label="Notifications"
            modifier="ghost"
            size="small"
            tooltip={false}
          />
        </PopoverTrigger>
        <PopoverContent
          align="end"
          aria-label="Notifications"
          className="w-80 flex-col p-1.5 pb-2"
        >
          <Text
            as="p"
            level="label-large"
            className="px-2.5 pt-2 pb-1.5 text-content-layout-1"
          >
            Notifications
          </Text>
          <SwitchRow
            id="notify-push"
            label={`Push to ${thisComputer()}`}
            checked={push}
            onChange={(on) => void setDesktopNotifications(on)}
          />
          {push && (
            <Row label="Test notification">
              <Button
                label="Send"
                aria-label="Send a test notification"
                modifier="ghost"
                size="small"
                onClick={() =>
                  showDesktopNotification({ title: 'RDST test notification' })
                }
              />
            </Row>
          )}
          {target ? (
            <Controls target={target} />
          ) : (
            <Text
              as="p"
              level="caption"
              className="px-2.5 py-2.5 text-content-layout-3"
            >
              Add a database to choose what it pushes. Nothing is sent by email.
            </Text>
          )}
          <div className="my-1 border-t border-border-layout-2" />
          <Row label="Mute rules">
            <Button
              label="Show"
              aria-label="Show mute rules"
              icon="chevron-right"
              iconPosition="right"
              modifier="ghost"
              size="small"
              onClick={() => {
                setOpen(false)
                setRulesOpen(true)
              }}
            />
          </Row>
        </PopoverContent>
      </Popover>
      <MuteRulesDialog open={rulesOpen} onClose={() => setRulesOpen(false)} />
    </>
  )
}
