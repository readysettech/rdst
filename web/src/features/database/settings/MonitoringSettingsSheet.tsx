// Monitoring settings sheet (brief 3.1): switches and levers that apply per control with a
// toast, plus the work done each day and storage used. Labels stay short; what a setting
// does sits in its tooltip. Daily limits show as settings, never as "N of M" counters.

import { cn } from '@rs/tailwind-base'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import {
  Drawer,
  DrawerContent,
  DrawerContentContainer,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@rs/ui-new/drawer'
import { ErrorState } from '@rs/ui-new/error-state'
import { SegmentedControl } from '@rs/ui-new/segmented-control'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { type ReactNode, useEffect, useRef } from 'react'
import { AiMonitoringPanel } from '@/features/monitor-kit/AiMonitoringStatus'
import { SERIES_COLOR } from '@/features/monitor-kit/charts'
import { formatBytes } from '@/features/monitor-kit/format'
import { MonitorWorkList } from '@/features/monitor-kit/MonitorWorkList'
import { InfoTip } from '@/features/queries/saved/InfoTip'
import {
  useAnalyzeJobs,
  useMonitorSettings,
  useMonitorUsage,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import type {
  MonitorSettings,
  MonitorSettingsUpdate,
  MonitorUsage,
} from '@/lib/monitor/types'
import { MonitoringSwitch } from './MonitoringSwitch'

const CAP_VALUES = [5, 10, 25, 50, 100, 200, 500]

/** The cap choices, plus the current value when it was set elsewhere. */
function capOptions(current: number) {
  const values = CAP_VALUES.includes(current)
    ? CAP_VALUES
    : [...CAP_VALUES, current].sort((a, b) => a - b)
  return values.map((value) => ({
    value: String(value),
    label: `Top ${value}`,
  }))
}

const NEWCOMER_VALUES = [5, 10, 25, 50, 100, 200]
const NEWCOMER_OFF = 'off'

/** Off, then the newcomer ceilings, plus the current value when it was set elsewhere. */
function newcomerOptions(current: number) {
  const values = NEWCOMER_VALUES.includes(current)
    ? NEWCOMER_VALUES
    : [...NEWCOMER_VALUES, current].sort((a, b) => a - b)
  return [
    { value: NEWCOMER_OFF, label: 'Off' },
    ...values.map((value) => ({
      value: String(value),
      label: value === 0 ? 'None' : String(value),
    })),
  ]
}

/** Said next to the top N setting: a click always runs and never counts. */
export const MANUAL_ANALYSIS_NOTE =
  'You can deep analyze any query yourself, as many times as you like, from its row or detail.'

const PUSH_LIMITS = [0, 1, 3, 5, 10] as const

/** The push limit choices, plus the current value when it was set elsewhere. */
function pushSegments(current: number) {
  const values: number[] = [...PUSH_LIMITS]
  if (!values.includes(current)) values.push(current)
  return values
    .sort((a, b) => a - b)
    .map((value) => ({
      value: String(value),
      label: value === 0 ? 'None' : String(value),
    }))
}

function runsQueries(settings: MonitorSettings) {
  return settings.analysis_mode
    ? settings.analysis_mode === 'execute'
    : settings.execute
}

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, hour) => ({
  value: String(hour),
  label: new Date(2026, 0, 1, hour).toLocaleTimeString('en-US', {
    hour: 'numeric',
  }),
}))

function Row({
  title,
  tip,
  control,
  htmlFor,
}: {
  title: string
  /** One gesture away: what the setting does, when the title alone doesn't say. */
  tip?: string
  control: ReactNode
  htmlFor?: string
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="flex min-w-0 items-center">
        <label
          htmlFor={htmlFor}
          className="text-label-medium text-content-layout-1"
        >
          {title}
        </label>
        {tip ? <InfoTip text={tip} /> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">{control}</div>
    </div>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-panel border border-border-layout-1 bg-surface-layout-1 px-4 py-1">
      <Text as="h3" level="subtitle-2" className="pt-3 text-content-layout-1">
        {title}
      </Text>
      <div className="divide-y divide-border-layout-1">{children}</div>
    </section>
  )
}

function Meter({
  label,
  used,
  cap,
  unit,
  color,
}: {
  label: string
  used: number
  cap: number | null
  unit: string
  color: string
}) {
  const fraction = cap ? Math.min(1, used / cap) : 0
  const near = cap != null && fraction >= 0.8
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-label-small text-content-layout-2">{label}</span>
        <span className="text-label-small text-content-layout-1 tabular-nums">
          {used.toLocaleString('en-US')}
          {cap != null && (
            <span className="text-content-layout-3">
              {' '}
              of {cap.toLocaleString('en-US')} {unit}
            </span>
          )}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-pill bg-surface-layout-2">
        <div
          className="h-full rounded-pill transition-[width] duration-slow ease-out"
          style={{
            width: `${Math.max(cap ? 2 : 0, fraction * 100)}%`,
            background: near ? 'var(--color-content-warning-plain)' : color,
          }}
        />
      </div>
    </div>
  )
}

/** One day's amount of work, without a limit beside it. */
function Count({ label, value }: { label: string; value: number }) {
  return (
    <div
      className="flex items-baseline justify-between gap-3"
      data-testid="usage-count"
    >
      <span className="text-label-small text-content-layout-2">{label}</span>
      <span className="text-label-small text-content-layout-1 tabular-nums">
        {value.toLocaleString('en-US')}
      </span>
    </div>
  )
}

function UsagePanel({
  usage,
  size,
  budget,
}: {
  usage: MonitorUsage
  size: number
  budget: number
}) {
  const today = usage.days[usage.days.length - 1]
  return (
    <div className="space-y-3 py-3">
      <Count label="Change checks today" value={today?.jev_calls ?? 0} />
      <Count label="Deep analyses today" value={today?.analyses ?? 0} />
      <Count label="Pushes today" value={today?.notifications ?? 0} />
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-label-small text-content-layout-2">
          AI calls, last 7 days
        </span>
        <span className="text-label-small text-content-layout-1 tabular-nums">
          {usage.days
            .reduce((sum, day) => sum + day.billable_calls, 0)
            .toLocaleString('en-US')}
        </span>
      </div>
      <div
        title={`${formatBytes(size)} of ${formatBytes(budget)}. The oldest minute readings roll up first when storage fills.`}
      >
        <Meter
          label="Storage"
          used={Math.round((size / (1024 * 1024)) * 10) / 10}
          cap={Math.round(budget / (1024 * 1024))}
          unit="MB"
          color={SERIES_COLOR.connections}
        />
      </div>
    </div>
  )
}

export function MonitoringSettingsSheet({
  target,
  open,
  onClose,
  focus = null,
}: {
  target: string
  open: boolean
  onClose: () => void
  /** Opens scrolled to one part: the analysis queue. */
  focus?: 'queue' | null
}) {
  const settings = useMonitorSettings(target)
  const usage = useMonitorUsage(target, 7)
  const queue = useAnalyzeJobs(target).data?.queue
  const queueRef = useRef<HTMLDivElement>(null)
  const hasQueue = Boolean(queue)
  useEffect(() => {
    if (!open || focus !== 'queue' || !hasQueue) return
    const frame = globalThis.requestAnimationFrame(() =>
      queueRef.current?.scrollIntoView?.({ block: 'start' })
    )
    return () => globalThis.cancelAnimationFrame(frame)
  }, [open, focus, hasQueue])
  const update = useUpdateMonitorSettings(target)
  const { toast } = useToast()
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

  return (
    <>
      <Drawer
        open={open}
        onOpenChange={(next) => (next ? undefined : onClose())}
        direction="right"
      >
        <DrawerContentContainer>
          {open && (
            <DrawerContent size="base" direction="right" className="p-0">
              <DrawerHeader className="border-b border-border-layout-1 p-5">
                <DrawerTitle>Monitoring settings</DrawerTitle>
                <DrawerDescription>{target}</DrawerDescription>
              </DrawerHeader>
              <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
                {settings.isError ? (
                  <ErrorState
                    title="Settings didn't load"
                    message="RDST couldn't read the monitoring settings for this target."
                    onRetry={() => void settings.refetch()}
                  />
                ) : !data ? (
                  <div className="space-y-3">
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-40 w-full" />
                  </div>
                ) : (
                  <>
                    <Group title="Monitoring">
                      <Row
                        title="Monitor this database"
                        tip="RDST reads query statistics every minute and tracks the top 10,000 queries. It only reads."
                        control={
                          <MonitoringSwitch
                            target={target}
                            withStatus={false}
                            warning={false}
                          />
                        }
                      />
                      {data.enabled ? (
                        <AiMonitoringPanel
                          target={target}
                          state={data.ai_monitoring}
                          reason={data.ai_reason}
                          since={data.ai_since}
                        />
                      ) : null}
                    </Group>

                    <Group title="Deep analysis">
                      <Row
                        title="Analyze automatically"
                        tip="Deep analysis reads a query's plan and suggests a fix, like an index."
                        control={
                          <BaseInputSwitch
                            name="monitor-deep"
                            checked={data.deep_analysis}
                            aria-label="Deep analysis"
                            onCheckedChange={(checked) =>
                              save({ deep_analysis: checked })
                            }
                          />
                        }
                      />
                      <Row
                        title="Top queries per day"
                        tip="When monitoring turns on, these get their first analysis within a few minutes. After that, they're re-analyzed as the ranking changes."
                        htmlFor="monitor-cap"
                        control={
                          <BaseInputSelect
                            id="monitor-cap"
                            aria-label="Top queries to analyze per day"
                            name="monitor-cap"
                            options={capOptions(data.analysis_daily_cap)}
                            value={String(data.analysis_daily_cap)}
                            onValueChange={(value) =>
                              save({ analysis_daily_cap: Number(value) })
                            }
                          />
                        }
                      />
                      <Row
                        title="New top queries per day"
                        tip="A query that enters the top for the first time is analyzed right away. Past this many in a day, it's picked up tomorrow if it's still near the top."
                        htmlFor="monitor-newcomer-cap"
                        control={
                          <BaseInputSelect
                            id="monitor-newcomer-cap"
                            aria-label="New top queries to analyze per day"
                            name="monitor-newcomer-cap"
                            options={newcomerOptions(
                              data.newcomer_daily_cap ?? 25
                            )}
                            value={
                              data.auto_analyze_new
                                ? String(data.newcomer_daily_cap ?? 25)
                                : NEWCOMER_OFF
                            }
                            onValueChange={(value) =>
                              save(
                                value === NEWCOMER_OFF
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
                      <Row
                        title="Measure real timings"
                        tip={
                          runsQueries(data)
                            ? 'RDST runs read-only SELECTs with EXPLAIN ANALYZE, each with a time limit.'
                            : 'Plans only: RDST runs EXPLAIN, and your queries never run.'
                        }
                        control={
                          <BaseInputSwitch
                            name="monitor-execute"
                            checked={runsQueries(data)}
                            aria-label="Measure real timings"
                            onCheckedChange={(checked) =>
                              save({
                                analysis_mode: checked
                                  ? 'execute'
                                  : 'plans_only',
                              })
                            }
                          />
                        }
                      />
                      <Text
                        as="p"
                        level="caption"
                        className="py-2.5 text-content-layout-3"
                        data-testid="manual-analysis-note"
                      >
                        {MANUAL_ANALYSIS_NOTE}
                      </Text>
                    </Group>

                    <Group title="Alerts and reports">
                      <Row
                        title="Check for changes"
                        tip="How often RDST looks for new and changed queries."
                        control={
                          <SegmentedControl
                            mode="radio"
                            size="small"
                            aria-label="Change check frequency"
                            value={String(data.attention_every_min)}
                            onValueChange={(value) =>
                              save({
                                attention_every_min: Number(value) as
                                  | 5
                                  | 15
                                  | 60,
                              })
                            }
                            segments={[
                              { value: '5', label: '5 min' },
                              { value: '15', label: '15 min' },
                              { value: '60', label: 'Hourly' },
                            ]}
                          />
                        }
                      />
                      <Row
                        title="Pushes a day"
                        tip="Past this limit, alerts still land in the Inbox and the nightly checkup."
                        control={
                          <SegmentedControl
                            mode="radio"
                            size="small"
                            aria-label="Pushes a day"
                            value={String(data.push_daily_cap)}
                            onValueChange={(value) =>
                              save({ push_daily_cap: Number(value) })
                            }
                            segments={pushSegments(data.push_daily_cap)}
                          />
                        }
                      />
                      <Row
                        title="Nightly checkup"
                        control={
                          <>
                            <div
                              className={cn(
                                !data.nightly_report && 'opacity-50'
                              )}
                            >
                              <BaseInputSelect
                                name="monitor-nightly-hour"
                                aria-label="Report time"
                                options={HOUR_OPTIONS}
                                value={String(data.nightly_hour)}
                                disabled={!data.nightly_report}
                                onValueChange={(value) =>
                                  save({ nightly_hour: Number(value) })
                                }
                              />
                            </div>
                            <BaseInputSwitch
                              name="monitor-nightly"
                              checked={data.nightly_report}
                              aria-label="Nightly checkup"
                              onCheckedChange={(checked) =>
                                save({ nightly_report: checked })
                              }
                            />
                          </>
                        }
                      />
                    </Group>

                    {queue && focus === 'queue' && (
                      <div ref={queueRef} className="scroll-mt-4">
                        <Group title="Analysis queue">
                          <MonitorWorkList
                            target={target}
                            defaultOpen
                            onOpened={onClose}
                          />
                        </Group>
                      </div>
                    )}

                    <Group title="Usage">
                      {usage.data ? (
                        <UsagePanel
                          usage={usage.data}
                          size={data.size_bytes}
                          budget={data.budget_bytes}
                        />
                      ) : usage.isError ? (
                        <Text
                          as="p"
                          level="body-small"
                          className="py-3 text-content-layout-2"
                        >
                          Usage isn't available right now.
                        </Text>
                      ) : (
                        <div className="space-y-3 py-3">
                          <Skeleton className="h-6 w-full" />
                          <Skeleton className="h-6 w-full" />
                        </div>
                      )}
                    </Group>
                  </>
                )}
              </div>
            </DrawerContent>
          )}
        </DrawerContentContainer>
      </Drawer>
    </>
  )
}
