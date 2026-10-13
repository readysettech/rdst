// Monitoring settings in the right pane: preference rows with the control at the right.
// The master switch carries the status and today's counts as its caption; Analysis,
// Report and Storage follow, Advanced stays folded, and "Stop monitoring" is the only
// footer action.
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Dropdown } from '@rs/ui-new/dropdown'
import { ErrorState } from '@rs/ui-new/error-state'
import { Scrollable } from '@rs/ui-new/scrollable'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { SidePane } from '@rs/ui-new/side-pane'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { Link } from '@tanstack/react-router'
import { type ReactNode, useState } from 'react'
import { AiMonitoringPanel } from '@/features/monitor-kit/AiMonitoringStatus'
import { formatBytes, plural } from '@/features/monitor-kit/format'
import {
  StateDot,
  type SwitcherTone,
} from '@/features/shared/filters/DatabaseFilter'
import {
  useMonitorSettings,
  useMonitorUsage,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import type {
  MonitorSettings,
  MonitorSettingsUpdate,
  MonitorUsage,
} from '@/lib/monitor/types'
import { EnableMonitoringDialog } from './EnableMonitoringDialog'
import { TurnOffMonitoringDialog } from './MonitoringSwitch'
import { SettingRow } from './SettingRow'

const TOP_CAPS = [5, 10, 15, 25, 50, 100]
const NEW_CAPS = [5, 10, 25, 50, 100, 200]
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)

export function hourLabel(hour: number): string {
  return new Date(2026, 0, 1, hour).toLocaleTimeString('en-US', {
    hour: 'numeric',
  })
}

/** "AI checks running. Today 12 analyses, 1 change check, 0 pushes." */
export function statusCaption(
  settings: MonitorSettings,
  usage: MonitorUsage | undefined
): { word: string; tone: SwitcherTone; counts: string | null } {
  const today = usage?.days[usage.days.length - 1]
  const counts = today
    ? `Today ${plural(today.analyses, 'analysis', 'analyses')}, ${plural(today.jev_calls, 'change check')}, ${plural(today.notifications ?? 0, 'push', 'pushes')}.`
    : null
  if (!settings.enabled) return { word: 'Not monitored', tone: 'off', counts }
  if (settings.ai_monitoring === 'on')
    return { word: 'AI checks running.', tone: 'live', counts }
  return { word: 'AI checks paused.', tone: 'warning', counts }
}

function Menu({
  label,
  options,
  onPick,
  ariaLabel,
}: {
  label: string
  options: { value: string; label: string; active?: boolean }[]
  onPick: (value: string) => void
  ariaLabel: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <Dropdown open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          icon="chevron-down"
          iconPosition="right"
          label={label}
          aria-label={`${ariaLabel}: ${label}`}
        />
      </Dropdown.Trigger>
      <Dropdown.Content
        align="end"
        className="min-w-36 rounded-panel bg-surface-primary-soft p-0"
      >
        <Scrollable className="max-h-80 p-1.5">
          {options.map((option) => (
            <Dropdown.Item
              key={option.value}
              label={option.label}
              active={option.active}
              className="h-8.5 rounded-md px-2.5 text-body-small"
              onClick={() => onPick(option.value)}
            />
          ))}
        </Scrollable>
      </Dropdown.Content>
    </Dropdown>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-6.5">
      <Text as="h4" level="caption" className="mb-0.5 text-content-layout-3">
        {title}
      </Text>
      {children}
    </div>
  )
}

export function MonitoringSettingsPane({
  target,
  onClose,
}: {
  target: string
  onClose: () => void
}) {
  const settings = useMonitorSettings(target)
  const usage = useMonitorUsage(target, 7)
  const update = useUpdateMonitorSettings(target)
  const { toast } = useToast()
  const [stopOpen, setStopOpen] = useState(false)
  const [enableOpen, setEnableOpen] = useState(false)
  const data = settings.data

  function save(change: MonitorSettingsUpdate) {
    update.mutate(change, {
      onSuccess: () =>
        toast({ title: 'Monitoring settings saved', variant: 'positive' }),
      onError: (error) =>
        toast({
          title: "Couldn't save monitoring settings",
          description: error instanceof Error ? error.message : undefined,
          variant: 'negative',
        }),
    })
  }

  const status = data ? statusCaption(data, usage.data) : null

  return (
    <SidePane
      aria-label={`Monitoring, ${target}`}
      chip={
        <span className="text-body-medium font-medium text-content-layout-1">
          Monitoring
        </span>
      }
      meta={<span>{target}</span>}
      onClose={onClose}
      footer={
        data?.enabled ? (
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Stop monitoring"
            onClick={() => setStopOpen(true)}
          />
        ) : undefined
      }
    >
      {settings.isError ? (
        <ErrorState
          tone="quiet"
          title="Settings didn't load"
          message="RDST couldn't read the monitoring settings for this target."
          onRetry={() => void settings.refetch()}
        />
      ) : !data || !status ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        <>
          {data.enabled ? (
            <AiMonitoringPanel
              target={target}
              state={data.ai_monitoring}
              reason={data.ai_reason}
              since={data.ai_since}
            />
          ) : null}
          <SettingRow
            title="Monitor this database"
            className="pt-1"
            line={
              <span className="flex items-center gap-1.5">
                <StateDot tone={status.tone} />
                {status.word}
                {status.counts ? ` ${status.counts}` : ''}
              </span>
            }
            control={
              <BaseInputSwitch
                name="monitor-enabled"
                aria-label="Monitor this database"
                checked={data.enabled}
                onCheckedChange={(checked) =>
                  checked ? setEnableOpen(true) : setStopOpen(true)
                }
              />
            }
          />

          <Group title="Analysis">
            <SettingRow
              title="Analyze automatically"
              line="Busiest queries first, new ones as they appear."
              control={
                <BaseInputSwitch
                  name="monitor-deep"
                  aria-label="Analyze automatically"
                  checked={data.deep_analysis}
                  onCheckedChange={(checked) =>
                    save({ deep_analysis: checked })
                  }
                />
              }
            />
            <SettingRow
              title="Measure real timings"
              line="Runs read-only SELECTs with a time limit."
              control={
                <BaseInputSwitch
                  name="monitor-execute"
                  aria-label="Measure real timings"
                  checked={data.analysis_mode === 'execute'}
                  onCheckedChange={(checked) =>
                    save({ analysis_mode: checked ? 'execute' : 'plans_only' })
                  }
                />
              }
            />
          </Group>

          <Group title="Report">
            <SettingRow
              title="Nightly report"
              line="Filed in the Inbox and pushed once."
              control={
                <>
                  <Menu
                    ariaLabel="Report time"
                    label={hourLabel(data.nightly_hour)}
                    options={HOURS.map((hour) => ({
                      value: String(hour),
                      label: hourLabel(hour),
                      active: hour === data.nightly_hour,
                    }))}
                    onPick={(value) => save({ nightly_hour: Number(value) })}
                  />
                  <BaseInputSwitch
                    name="monitor-nightly"
                    aria-label="Nightly report"
                    checked={data.nightly_report}
                    onCheckedChange={(checked) =>
                      save({ nightly_report: checked })
                    }
                  />
                </>
              }
            />
          </Group>

          <Group title="Storage">
            <SettingRow
              title="Keep readings"
              line={`${formatBytes(data.size_bytes)} of ${formatBytes(data.budget_bytes)} used.`}
              control={
                <Text
                  as="span"
                  level="caption"
                  className="text-content-layout-2"
                >
                  7 days
                </Text>
              }
            />
          </Group>

          <Disclosure title="Advanced" className="mt-6.5">
            <SettingRow
              title="Re-analyze the top queries each day"
              control={
                <Menu
                  ariaLabel="Top queries each day"
                  label={String(data.analysis_daily_cap)}
                  options={[...new Set([...TOP_CAPS, data.analysis_daily_cap])]
                    .sort((a, b) => a - b)
                    .map((value) => ({
                      value: String(value),
                      label: String(value),
                      active: value === data.analysis_daily_cap,
                    }))}
                  onPick={(value) =>
                    save({ analysis_daily_cap: Number(value) })
                  }
                />
              }
            />
            <SettingRow
              title="Analyze new queries, up to a day"
              control={
                <Menu
                  ariaLabel="New queries a day"
                  label={
                    data.auto_analyze_new
                      ? String(data.newcomer_daily_cap)
                      : 'Off'
                  }
                  options={[
                    {
                      value: 'off',
                      label: 'Off',
                      active: !data.auto_analyze_new,
                    },
                    ...[...new Set([...NEW_CAPS, data.newcomer_daily_cap])]
                      .sort((a, b) => a - b)
                      .map((value) => ({
                        value: String(value),
                        label: String(value),
                        active:
                          data.auto_analyze_new &&
                          value === data.newcomer_daily_cap,
                      })),
                  ]}
                  onPick={(value) =>
                    save(
                      value === 'off'
                        ? { auto_analyze_new: false }
                        : {
                            auto_analyze_new: true,
                            newcomer_daily_cap: Number(value),
                          }
                    )
                  }
                />
              }
            />
            <SettingRow
              title="Check for changes"
              last
              control={
                <SegmentedControl
                  mode="radio"
                  size="small"
                  aria-label="Check for changes"
                  value={String(data.attention_every_min)}
                  onValueChange={(value) =>
                    save({ attention_every_min: Number(value) as 5 | 15 | 60 })
                  }
                  segments={[
                    { value: '5', label: '5 min' },
                    { value: '15', label: '15 min' },
                    { value: '60', label: 'Hourly' },
                  ]}
                  className="border-transparent bg-transparent"
                />
              }
            />
          </Disclosure>

          <Text as="p" level="caption" className="mt-7 text-content-layout-3">
            Notifications are set in the Inbox.{' '}
            <Link
              to="/configure"
              search={{ panel: 'connections', edit: target } as never}
              className="border-b border-border-layout-2 text-content-layout-2 hover:border-content-layout-2"
            >
              Connection
            </Link>
          </Text>
        </>
      )}
      <TurnOffMonitoringDialog
        target={target}
        open={stopOpen}
        onClose={() => setStopOpen(false)}
      />
      <EnableMonitoringDialog
        target={target}
        open={enableOpen}
        onClose={() => setEnableOpen(false)}
        openDatabase={false}
      />
    </SidePane>
  )
}
