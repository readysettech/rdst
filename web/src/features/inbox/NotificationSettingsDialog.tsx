import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Dropdown } from '@rs/ui-new/dropdown'
import { IconButton } from '@rs/ui-new/icon-button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useState } from 'react'
import { isContainerRuntime, thisComputer, thisDevice } from '@/lib/desktop'
import {
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import type { NotifyEvents, QuietHours } from '@/lib/monitor/types'
import { useDesktopNotificationHealth } from '@/lib/useDesktopNotificationHealth'
import {
  sendTestNotification,
  setDesktopNotifications,
  useDesktopNotificationsOn,
} from './desktopNotifications'
import { MuteRulesDialog } from './MuteRulesList'
import {
  sendSlackTest,
  useSaveSlackWebhook,
  useSlackSettings,
} from './slackNotifications'

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
function Controls({ target, slack }: { target: string; slack: boolean }) {
  const settings = useMonitorSettings(target)
  const update = useUpdateMonitorSettings(target)
  // Nothing pushes while both Push to this computer and Slack are off, so no switch reads on.
  const desktop = useDesktopNotificationsOn()
  const push = desktop || slack
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
        {pushCaption(desktop, slack)}
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

function pushCaption(desktop: boolean, slack: boolean): string {
  if (desktop && slack)
    return 'Pushed once as a desktop notification and to Slack. Nothing is sent by email.'
  if (slack) return 'Pushed once to Slack. Nothing is sent by email.'
  if (desktop)
    return 'Pushed once as a desktop notification. Nothing is sent by email.'
  return isContainerRuntime()
    ? 'Nothing is pushed until Slack is set up. Alerts still wait in the Inbox.'
    : `Push to ${thisComputer()} is off, so nothing is pushed. Nothing is sent by email.`
}

/** Slack: the webhook the server posts pushes to, a test, and the last delivery problem. */
function SlackSection() {
  const settings = useSlackSettings()
  const save = useSaveSlackWebhook()
  const [url, setUrl] = useState('')
  const [result, setResult] = useState<{ problem: string | null } | null>(null)
  const data = settings.data
  if (settings.isError) return null
  const fromEnv = data?.source === 'env'
  const problem = save.error instanceof Error ? save.error.message : null
  return (
    <div className="border-b border-border-layout-2 px-2.5 py-2">
      <div className="flex min-h-9 items-center gap-3 text-body-small text-content-layout-1">
        <span className="flex-1">Slack</span>
        {data?.configured ? (
          <>
            <Button
              label="Send test"
              aria-label="Send a test message to Slack"
              modifier="ghost"
              size="small"
              onClick={() =>
                void sendSlackTest().then((p) => setResult({ problem: p }))
              }
            />
            {!fromEnv && (
              <Button
                label="Remove"
                aria-label="Remove the Slack webhook"
                modifier="ghost"
                size="small"
                disabled={save.isPending}
                onClick={() => {
                  setResult(null)
                  save.mutate(null)
                }}
              />
            )}
          </>
        ) : null}
      </div>
      {data?.configured ? (
        <Text as="p" level="caption" className="text-content-layout-3">
          {fromEnv
            ? 'Pushes go to the Slack webhook in RDST_SLACK_WEBHOOK_URL.'
            : 'Pushes go to your Slack webhook.'}
        </Text>
      ) : (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            setResult(null)
            save.mutate(url.trim(), { onSuccess: () => setUrl('') })
          }}
        >
          <Text as="p" level="caption" className="text-content-layout-3">
            {isContainerRuntime()
              ? 'Paste a Slack incoming webhook URL to get pushes in a channel or DM while nobody has RDST open.'
              : 'Paste a Slack incoming webhook URL to also get pushes in a channel or DM.'}
          </Text>
          <BaseInputText
            name="slack-webhook-url"
            type="password"
            aria-label="Slack webhook URL"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://hooks.slack.com/services/..."
            autoComplete="off"
            disabled={save.isPending}
          />
          <div className="flex justify-end gap-1">
            <Button
              type="button"
              label="Test"
              aria-label="Send a test message to this webhook"
              modifier="ghost"
              size="small"
              disabled={!url.trim()}
              onClick={() =>
                void sendSlackTest(url.trim()).then((p) =>
                  setResult({ problem: p })
                )
              }
            />
            <Button
              type="submit"
              label="Save"
              size="small"
              disabled={!url.trim() || save.isPending}
            />
          </div>
        </form>
      )}
      {(result || problem || data?.last_error) && (
        <Text asChild level="caption">
          <output
            className={`block pt-1.5 ${result?.problem || problem || (!result && data?.last_error) ? 'text-content-warning-soft' : 'text-content-layout-3'}`}
          >
            {result
              ? (result.problem ?? 'Sent. Check the Slack channel or DM.')
              : (problem ?? `Last message failed: ${data?.last_error}`)}
          </output>
        </Text>
      )}
    </div>
  )
}

/** The bell: what pushes to the desktop, the quiet hours, and the mute rules. */
export function NotificationSettings({ target }: { target?: string }) {
  const [open, setOpen] = useState(false)
  const [rulesOpen, setRulesOpen] = useState(false)
  // The last test's outcome: null once shown, else why it couldn't be.
  const [tested, setTested] = useState<{ problem: string | null } | null>(null)
  const push = useDesktopNotificationsOn()
  const slack = useSlackSettings().data?.configured ?? false
  const health = useDesktopNotificationHealth()
  const blocked = push && health?.blocked ? health.message : null
  return (
    <>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          setTested(null)
        }}
      >
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
            label={`Push to ${thisDevice()}`}
            checked={push}
            onChange={(on) => void setDesktopNotifications(on)}
          />
          {blocked && (
            <Text
              as="p"
              level="caption"
              className="px-2.5 py-2 text-content-warning-soft"
            >
              {blocked}
            </Text>
          )}
          {push && (
            <Row label="Test notification">
              <Button
                label="Send"
                aria-label="Send a test notification"
                modifier="ghost"
                size="small"
                onClick={() =>
                  void sendTestNotification().then((problem) =>
                    setTested({ problem })
                  )
                }
              />
            </Row>
          )}
          {push && tested && (
            <Text asChild level="caption">
              <output
                className={`block px-2.5 py-2 ${tested.problem ? 'text-content-warning-soft' : 'text-content-layout-3'}`}
              >
                {tested.problem ??
                  "Sent. If it didn't appear, check the system's notification settings."}
              </output>
            </Text>
          )}
          <SlackSection />
          {target ? (
            <Controls target={target} slack={slack} />
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
