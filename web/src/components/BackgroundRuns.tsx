import { Icon } from '@rs/ui-new/icon'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { Spinner } from '@rs/ui-new/spinner'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import {
  analysisSummary,
  MonitorWorkList,
  workCount,
} from '../features/monitor-kit/MonitorWorkList'
import {
  isQueuedRun,
  reattachBackgroundRuns,
  useBackgroundRuns as useBackgroundRunsStore,
} from '../lib/backgroundRuns'
import { useMonitoringLimit, useMonitorWork } from '../lib/monitor/hooks'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { resumeAuditSessions } from '../lib/useAudit'
import {
  BackgroundJobList,
  detailFor,
  isActive,
  orderRuns,
  severityFor,
  titleFor,
  useOpenBackgroundRun,
  useStopJobConfirm,
} from './BackgroundJobs'
import { TrialRegistrationDialog } from './TrialRegistrationDialog'

/**
 * The app's Analyses chip: monitoring's deep analyses and reports across every database,
 * plus the runs the user started (which survive route changes and browser reloads). The
 * count is what's running or queued; finished work never counts.
 */
export function BackgroundRuns() {
  const allRuns = useBackgroundRunsStore()
  // Health checks list here like every other kind; the banner is an additional
  // indicator for the run, not a replacement for its card.
  const visibleRuns = allRuns.filter((run) => !run.hidden)
  const latestRun = visibleRuns[visibleRuns.length - 1]
  const runs = orderRuns(visibleRuns)
  const triggerRun = runs[0]
  const queryClient = useQueryClient()
  const [trialOpen, setTrialOpen] = useState(false)
  const [jobsOpen, setJobsOpen] = useState(false)
  const closeJobs = useCallback(() => setJobsOpen(false), [])
  const openRun = useOpenBackgroundRun(closeJobs)
  const stopJob = useStopJobConfirm()
  const monitored = (useMonitoringLimit().data?.targets.length ?? 0) > 0
  const work = useMonitorWork(monitored).data
  const monitorPending = monitored ? workCount(work) : 0

  useEffect(() => {
    reattachBackgroundRuns()
    resumeAuditSessions()
  }, [])

  if (!latestRun || !triggerRun) {
    if (!monitored) return null
    return (
      <Popover open={jobsOpen} onOpenChange={setJobsOpen}>
        <PopoverTrigger asChild>
          <Pressable
            type="button"
            className="group flex w-full min-w-0 cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm font-medium text-content-layout-2 transition-all duration-150 hover:bg-surface-layout-2 hover:text-content-layout-1"
            aria-label={`Open analyses: ${analysisSummary(work)}`}
            data-testid="jobs-trigger"
          >
            <span className="relative grid h-4 w-4 shrink-0 place-items-center">
              <Icon
                name="sparkles"
                label=""
                className="h-4 w-4 text-content-layout-3"
              />
              {work?.running.length ? (
                <span
                  aria-hidden="true"
                  className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-content-rising-plain mk-breathe"
                />
              ) : null}
            </span>
            <Text level="caption" className="text-content-layout-1">
              Analyses
            </Text>
            <Text
              level="caption"
              className="min-w-0 flex-1 truncate text-content-layout-3"
            >
              {monitorPending
                ? analysisSummary(work)
                : work?.done_today_count
                  ? `${work.done_today_count} done today`
                  : null}
            </Text>
            {monitorPending ? (
              <span
                className="inline-flex min-w-5 shrink-0 items-center justify-center rounded-full bg-surface-primary-soft px-1.5 py-0.5 text-[11px] leading-none text-content-primary-soft"
                aria-hidden="true"
              >
                {monitorPending > 99 ? '99+' : monitorPending}
              </span>
            ) : null}
          </Pressable>
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          className="max-h-[var(--radix-popover-content-available-height,80vh)] w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-y-auto p-0"
          aria-label="Analyses"
        >
          <AnalysesHeading summary={analysisSummary(work)} />
          <MonitorWorkList onOpened={closeJobs} defaultOpen />
        </PopoverContent>
      </Popover>
    )
  }

  const queuedCount = runs.filter(isQueuedRun).length
  const activeCount = runs.filter(isActive).length
  const pendingCount = activeCount + queuedCount
  const triggerSeverity = severityFor(triggerRun)
  const triggerTone =
    triggerSeverity === 'error'
      ? 'bg-surface-negative-soft/20 hover:bg-surface-negative-soft/30'
      : triggerSeverity === 'warning'
        ? 'bg-surface-warning-soft/20 hover:bg-surface-warning-soft/30'
        : 'hover:bg-surface-layout-2 hover:text-content-layout-1'
  const triggerTextTone =
    triggerSeverity === 'error'
      ? 'text-content-negative-soft'
      : triggerSeverity === 'warning'
        ? 'text-content-warning-soft'
        : 'text-content-layout-1'

  return (
    <>
      <Popover open={jobsOpen} onOpenChange={setJobsOpen}>
        <PopoverTrigger asChild>
          <Pressable
            type="button"
            className={`group flex w-full min-w-0 cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm font-medium text-content-layout-2 transition-all duration-150 ${triggerTone}`}
            aria-label={`Open jobs: ${titleFor(triggerRun)}. ${detailFor(triggerRun)} ${runs.length} total`}
            data-testid="jobs-trigger"
          >
            {triggerSeverity === 'error' ? (
              <Icon
                name="alert"
                label="Job error"
                data-testid="job-error-icon"
                className="h-4 w-4 shrink-0 text-content-negative-soft"
              />
            ) : triggerSeverity === 'warning' ? (
              <Icon
                name="alert"
                label="Job warning"
                data-testid="job-warning-icon"
                className="h-4 w-4 shrink-0 text-content-warning-soft"
              />
            ) : pendingCount > 0 ? (
              <span
                className="shrink-0"
                data-testid="running-job-spinner"
                aria-hidden="true"
              >
                <Spinner color="primary-soft" />
              </span>
            ) : (
              <Icon
                name="notification"
                label="Jobs"
                className="h-4 w-4 shrink-0 text-content-layout-3 transition-transform group-hover:scale-110"
              />
            )}
            <VStack className="min-w-0 flex-1 items-start gap-0 overflow-hidden">
              <Text
                level="caption"
                className={`w-full truncate ${triggerTextTone}`}
              >
                {titleFor(triggerRun)}
              </Text>
              <Text
                level="caption"
                className={`w-full line-clamp-2 whitespace-normal ${
                  triggerSeverity === 'error' || triggerSeverity === 'warning'
                    ? triggerTextTone
                    : 'text-content-layout-3'
                }`}
              >
                {detailFor(triggerRun)}
              </Text>
            </VStack>
            <span
              className={`inline-flex min-w-5 shrink-0 items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] leading-none ${
                triggerSeverity === 'error'
                  ? 'bg-surface-negative-soft text-content-negative-soft'
                  : triggerSeverity === 'warning'
                    ? 'bg-surface-warning-soft text-content-warning-soft'
                    : pendingCount > 0
                      ? 'bg-surface-primary-soft text-content-primary-soft'
                      : 'bg-surface-layout-2 text-content-layout-2'
              }`}
              aria-hidden="true"
            >
              {runs.length + monitorPending > 99
                ? '99+'
                : runs.length + monitorPending}
            </span>
          </Pressable>
        </PopoverTrigger>
        {/* Opening upward over the sidebar keeps the list off the page it is
            reporting on: a job's own progress cards were what the popover
            used to land on (B-09). */}
        <PopoverContent
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          className="max-h-[var(--radix-popover-content-available-height,80vh)] w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-y-auto p-0"
          aria-label="Background jobs"
        >
          {monitored ? (
            <div className="border-b border-border-layout-1">
              <AnalysesHeading summary={analysisSummary(work)} />
              <MonitorWorkList onOpened={closeJobs} />
            </div>
          ) : null}
          <BackgroundJobList
            runs={runs}
            onNeedsKey={() => {
              setJobsOpen(false)
              setTrialOpen(true)
            }}
            onOpen={openRun}
            onStop={(run) => {
              setJobsOpen(false)
              stopJob.requestStop(run)
            }}
          />
        </PopoverContent>
      </Popover>
      {stopJob.dialog}
      <TrialRegistrationDialog
        isOpen={trialOpen}
        onClose={() => setTrialOpen(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          setTrialOpen(false)
        }}
      />
    </>
  )
}

function AnalysesHeading({ summary }: { summary: string }) {
  return (
    <div className="border-b border-border-layout-1 px-4 py-3">
      <Text level="label-small" className="text-content-layout-1">
        Analyses
      </Text>
      <Text level="caption" className="block text-content-layout-3">
        {summary} across all your databases
      </Text>
    </div>
  )
}
