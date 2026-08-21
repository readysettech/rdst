import { Icon } from '@rs/ui-new/icon'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { useTarget } from '../hooks/useTarget'
import {
  acknowledgeBackgroundRun,
  type BackgroundRunState,
  cancelBackgroundRun,
  dismissBackgroundRun,
  isAuditKind,
  isHealthCheckKind,
  isQueuedRun,
  reattachBackgroundRuns,
  useBackgroundRuns,
} from '../lib/backgroundRuns'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { resumeAuditSessions } from '../lib/useAudit'
import { TrialRegistrationDialog } from './TrialRegistrationDialog'

function titleFor(run: BackgroundRunState): string {
  if (run.kind === 'bootstrap') return `Setting up ${run.target}`
  if (run.kind === 'cache_test' || run.kind === 'speed_test')
    return `Testing ${run.queryLabel || run.queryHash || run.target}`
  if (run.kind === 'load_test') return `Load test · ${run.target}`
  if (run.kind === 'audit') return `Health check on ${run.target}`
  if (run.kind === 'fleet_audit') return 'Fleet health check'
  if (run.kind === 'audit_capture') return `Capturing ${run.target}`
  if (run.kind === 'cache_compare')
    return `Comparing ${run.queryLabel || run.queryHash || run.target}`
  if (run.kind === 'analyze')
    return `Analyzing ${run.queryLabel || run.queryHash || run.target}`
  return `Annotating ${run.target}`
}

function doneLabel(run: BackgroundRunState): string {
  if (run.kind === 'bootstrap') return `${run.target} is ready`
  if (run.kind === 'cache_test' || run.kind === 'speed_test')
    return run.message || 'Performance test complete'
  if (run.kind === 'load_test') return run.message || 'Load test complete'
  if (run.kind === 'fleet_audit')
    return run.message || 'Fleet health check complete'
  if (isAuditKind(run.kind))
    return run.message || `${run.target} health check complete`
  return run.message || `${run.target} annotation complete`
}

function terminalLabel(run: BackgroundRunState): string {
  if (run.status === 'cancelled') {
    if (run.kind === 'cache_test' || run.kind === 'speed_test')
      return 'Performance test cancelled'
    if (run.kind === 'cache_compare') return 'Cache comparison cancelled'
    if (isHealthCheckKind(run.kind)) return 'Health check cancelled'
  }
  if (run.status !== 'done')
    return run.message || 'Background task did not finish'
  if (run.kind === 'cache_compare')
    return run.message || 'Cache comparison complete'
  if (run.kind === 'analyze') return 'Analysis complete'
  return doneLabel(run)
}

function isTerminal(run: BackgroundRunState): boolean {
  return ['done', 'partial', 'failed', 'cancelled', 'interrupted'].includes(
    run.status
  )
}

function isActive(run: BackgroundRunState): boolean {
  return (
    !isQueuedRun(run) &&
    (run.status === 'running' ||
      run.status === 'reconnecting' ||
      run.status === 'stopping')
  )
}

type RunSeverity = 'error' | 'warning' | 'running' | 'neutral'

function severityFor(run: BackgroundRunState): RunSeverity {
  if (run.status === 'failed' || run.status === 'interrupted') return 'error'
  if (run.status === 'needs_key' || run.status === 'partial' || run.hasWarnings)
    return 'warning'
  if (
    run.status === 'running' ||
    run.status === 'reconnecting' ||
    run.status === 'stopping'
  )
    return 'running'
  return 'neutral'
}

const SEVERITY_PRIORITY: Record<RunSeverity, number> = {
  error: 0,
  warning: 1,
  running: 2,
  neutral: 3,
}

function needsKeyDetail(): string {
  return 'Add an Anthropic key or start a free trial.'
}

function detailFor(run: BackgroundRunState): string {
  if (run.status === 'needs_key') return needsKeyDetail()
  if (run.status === 'reconnecting') return 'Reconnecting to background job...'
  if (
    run.status === 'running' &&
    run.current !== null &&
    run.total !== null &&
    run.total > 0
  ) {
    return isOpenable(run)
      ? `${run.message} · ${run.current}%`
      : `${run.current} of ${run.total} tables`
  }
  return isTerminal(run) ? terminalLabel(run) : run.message
}

/** Kinds whose card opens the screen showing that job's result. */
function isOpenable(run: BackgroundRunState): boolean {
  return (
    run.kind === 'cache_test' ||
    run.kind === 'speed_test' ||
    run.kind === 'load_test' ||
    run.kind === 'cache_compare' ||
    run.kind === 'analyze' ||
    isHealthCheckKind(run.kind)
  )
}

function RunCard({
  run,
  onNeedsKey,
  onOpen,
}: {
  run: BackgroundRunState
  onNeedsKey: () => void
  onOpen: () => void
}) {
  if (run.status === 'needs_key') {
    return (
      <Pressable
        type="button"
        onClick={onNeedsKey}
        className="w-full min-w-0 cursor-pointer rounded-lg bg-surface-warning-soft/20 px-3 py-2 text-left"
        data-testid={`background-run-${run.runId}`}
      >
        <HStack className="items-center gap-2">
          <Icon
            name="alert"
            label="Key needed"
            className="h-3.5 w-3.5 shrink-0 text-content-warning-soft"
          />
          <VStack className="min-w-0 flex-1 items-start gap-0">
            <Text
              level="caption"
              className="truncate text-content-warning-soft"
            >
              {titleFor(run)}
            </Text>
            <Text
              level="caption"
              className="whitespace-normal text-content-layout-3"
            >
              {needsKeyDetail()}
            </Text>
          </VStack>
        </HStack>
      </Pressable>
    )
  }

  if (
    run.status === 'running' ||
    run.status === 'reconnecting' ||
    run.status === 'stopping'
  ) {
    return (
      <div
        className="min-w-0 rounded-lg bg-surface-primary-soft/20 px-3 py-2"
        data-testid={`background-run-${run.runId}`}
      >
        <HStack className="items-start gap-2">
          <span className="mt-0.5 shrink-0">
            <Spinner color="primary-soft" />
          </span>
          <Pressable
            type="button"
            onClick={isOpenable(run) ? onOpen : undefined}
            className={`min-w-0 flex-1 text-left ${
              isOpenable(run) ? 'cursor-pointer' : 'cursor-default'
            }`}
          >
            <VStack className="min-w-0 items-start gap-0">
              <Text
                level="caption"
                className="w-full truncate text-content-primary-soft"
              >
                {titleFor(run)}
              </Text>
              <Text
                level="caption"
                className="w-full truncate text-content-layout-3"
              >
                {detailFor(run)}
              </Text>
            </VStack>
          </Pressable>
          <Pressable
            type="button"
            onClick={() => void cancelBackgroundRun(run.runId)}
            className="cursor-pointer text-content-layout-3 hover:text-content-layout-1"
            aria-label={`Cancel ${titleFor(run)}`}
          >
            <Icon name="close" label="" className="h-3.5 w-3.5" />
          </Pressable>
        </HStack>
      </div>
    )
  }

  const succeeded = run.status === 'done'
  const partial = run.status === 'partial'
  const tone = succeeded
    ? 'bg-surface-positive-soft/20'
    : partial
      ? 'bg-surface-warning-soft/20'
      : 'bg-surface-negative-soft/20'
  const content = (
    <HStack className="min-w-0 items-start gap-2">
      <Icon
        name={succeeded ? 'tick' : 'alert'}
        label={
          succeeded
            ? 'Complete'
            : partial
              ? 'Partially complete'
              : 'Background task issue'
        }
        className={`h-3.5 w-3.5 shrink-0 ${
          succeeded
            ? 'text-content-positive-soft'
            : partial
              ? 'text-content-warning-soft'
              : 'text-content-negative-soft'
        }`}
      />
      <VStack className="min-w-0 flex-1 items-start gap-0">
        <Text level="caption" className="w-full truncate text-content-layout-1">
          {titleFor(run)}
        </Text>
        <Text
          level="caption"
          className={`w-full ${succeeded ? 'truncate' : 'whitespace-normal'} ${
            succeeded
              ? 'text-content-positive-soft'
              : partial
                ? 'text-content-warning-soft'
                : 'text-content-negative-soft'
          }`}
        >
          {terminalLabel(run)}
        </Text>
      </VStack>
    </HStack>
  )

  return (
    <div
      className={`flex min-w-0 items-center rounded-lg px-3 py-2 ${tone}`}
      data-testid={`background-run-${run.runId}`}
    >
      <Pressable
        type="button"
        onClick={onOpen}
        className="min-w-0 flex-1 cursor-pointer overflow-hidden text-left"
        title={isOpenable(run) ? 'View results' : 'Dismiss job'}
      >
        {content}
      </Pressable>
      <Pressable
        type="button"
        onClick={() => dismissBackgroundRun(run.runId)}
        className="ml-2 shrink-0 cursor-pointer text-content-layout-3 hover:text-content-layout-1"
        aria-label={`Dismiss ${titleFor(run)}`}
      >
        <Icon name="close" label="" className="h-3.5 w-3.5" />
      </Pressable>
    </div>
  )
}

/** Process-wide background tasks survive route changes and browser reloads. */
export function BackgroundRuns() {
  const allRuns = useBackgroundRuns()
  // Health checks list here like every other kind; the banner is an additional
  // indicator for the run, not a replacement for its card.
  const visibleRuns = allRuns.filter((run) => !run.hidden)
  const latestRun = visibleRuns[visibleRuns.length - 1]
  const runs = visibleRuns
    .map((run, index) => ({ run, index }))
    .sort(
      (a, b) =>
        SEVERITY_PRIORITY[severityFor(a.run)] -
          SEVERITY_PRIORITY[severityFor(b.run)] || b.index - a.index
    )
    .map(({ run }) => run)
  const triggerRun = runs[0]
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { setTarget } = useTarget()
  const [trialOpen, setTrialOpen] = useState(false)
  const [jobsOpen, setJobsOpen] = useState(false)

  useEffect(() => {
    reattachBackgroundRuns()
    resumeAuditSessions()
  }, [])

  if (!latestRun || !triggerRun) return null

  const queuedCount = runs.filter(isQueuedRun).length
  const activeCount = runs.filter(isActive).length
  const pendingCount = activeCount + queuedCount
  const errorCount = runs.filter((run) => severityFor(run) === 'error').length
  const warningCount = runs.filter(
    (run) => severityFor(run) === 'warning'
  ).length
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

  const openRun = (run: BackgroundRunState) => {
    if (isTerminal(run)) {
      if (isOpenable(run)) acknowledgeBackgroundRun(run.runId)
      else dismissBackgroundRun(run.runId)
    }
    setJobsOpen(false)
    if (isHealthCheckKind(run.kind)) {
      // A fleet run spans many targets, so it carries a scope label rather
      // than a target the sidebar could switch to.
      if (isAuditKind(run.kind)) setTarget(run.target)
      void (run.snapshotId
        ? navigate({
            to: '/audit/runs/$runId',
            params: { runId: run.snapshotId },
          })
        : navigate({ to: '/audit' }))
      return
    }
    if (run.kind === 'load_test') {
      setTarget(run.target)
      void navigate({
        to: '/cache',
        search: { view: 'load-test', run: run.runId },
      })
      return
    }
    if (run.kind === 'cache_compare') {
      setTarget(run.target)
      void navigate({ to: '/cache', search: { view: 'compare' } })
      return
    }
    // The analyze drawer is URL-owned, so the job's own link reopens it —
    // attaching to the run whether it is still measuring or already finished.
    if (run.kind === 'analyze') {
      if (run.target) setTarget(run.target)
      void navigate({
        to: '/queries',
        search: run.queryHash ? { analyze: run.queryHash } : {},
      })
      return
    }
    if (
      (run.kind !== 'cache_test' && run.kind !== 'speed_test') ||
      !run.queryHash
    )
      return
    setTarget(run.target)
    // The hash is what reveals the query; a status filter on top of it could
    // only hide the row the job is pointing at.
    void navigate({
      to: '/queries',
      search: { hash: run.queryHash, run: run.runId },
    })
  }

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
              {runs.length > 99 ? '99+' : runs.length}
            </span>
          </Pressable>
        </PopoverTrigger>
        <PopoverContent
          side="right"
          align="end"
          sideOffset={12}
          collisionPadding={12}
          className="w-80 max-w-[calc(100vw-1.5rem)] flex-col p-0"
          aria-label="Background jobs"
        >
          <HStack className="items-center justify-between border-b border-border-layout-1 px-4 py-3">
            <VStack className="items-start gap-0">
              <Text level="label-small" className="text-content-layout-1">
                Jobs
              </Text>
              <Text level="caption" className="text-content-layout-3">
                {errorCount > 0
                  ? `${errorCount} ${errorCount === 1 ? 'job has' : 'jobs have'} failed`
                  : warningCount > 0
                    ? `${warningCount} ${warningCount === 1 ? 'job needs' : 'jobs need'} attention`
                    : activeCount > 0 && queuedCount > 0
                      ? `${activeCount} running · ${queuedCount} queued`
                      : activeCount > 0
                        ? `${activeCount} ${activeCount === 1 ? 'job' : 'jobs'} running`
                        : queuedCount > 0
                          ? `${queuedCount} ${queuedCount === 1 ? 'job' : 'jobs'} queued`
                          : 'No jobs running'}
              </Text>
            </VStack>
            <Text level="caption" className="text-content-layout-3">
              {runs.length} total
            </Text>
          </HStack>
          <VStack className="max-h-[min(32rem,calc(100vh-2rem))] items-stretch gap-2 overflow-y-auto p-2">
            {runs.map((run) => (
              <RunCard
                key={run.runId}
                run={run}
                onNeedsKey={() => {
                  setJobsOpen(false)
                  setTrialOpen(true)
                }}
                onOpen={() => openRun(run)}
              />
            ))}
          </VStack>
        </PopoverContent>
      </Popover>
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
