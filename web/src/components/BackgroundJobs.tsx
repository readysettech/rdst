/**
 * The job list itself, independent of where it is opened from.
 *
 * The sidebar chip is one host; the analyze drawer is another, because a modal
 * drawer takes the sidebar's pointer events with it and the list has to stay
 * reachable exactly while long work is in flight (B-08).
 */

import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useNavigate } from '@tanstack/react-router'
import { type ReactNode, useCallback, useState } from 'react'

import { useTarget } from '../hooks/useTarget'
import {
  acknowledgeBackgroundRun,
  type BackgroundRunState,
  cancelBackgroundRun,
  dismissBackgroundRun,
  isAuditKind,
  isHealthCheckKind,
  isQueuedRun,
  useBackgroundRuns as useBackgroundRunsStore,
} from '../lib/backgroundRuns'

export function titleFor(run: BackgroundRunState): string {
  if (run.kind === 'bootstrap') return `Setting up ${run.target}`
  if (run.kind === 'cache_test' || run.kind === 'speed_test')
    return `Load test · ${run.queryLabel || run.queryHash || run.target}`
  if (run.kind === 'load_test') return `Load test · ${run.target}`
  if (run.kind === 'audit') return `Health check on ${run.target}`
  if (run.kind === 'fleet_audit') return 'Fleet health check'
  if (run.kind === 'audit_capture') return `Capturing ${run.target}`
  if (run.kind === 'cache_compare')
    return `Comparing ${run.queryLabel || run.queryHash || run.target} against Readyset`
  if (run.kind === 'analyze')
    return `Analyzing ${run.queryLabel || run.queryHash || run.target}`
  return `Annotating ${run.target}`
}

function doneLabel(run: BackgroundRunState): string {
  if (run.kind === 'bootstrap') return `${run.target} is ready`
  if (run.kind === 'cache_test' || run.kind === 'speed_test')
    return run.message || 'Load test complete'
  if (run.kind === 'load_test') return run.message || 'Load test complete'
  if (run.kind === 'fleet_audit')
    return run.message || 'Fleet health check complete'
  if (isAuditKind(run.kind))
    return run.message || `${run.target} health check complete`
  return run.message || `${run.target} annotation complete`
}

export function terminalLabel(run: BackgroundRunState): string {
  if (run.status === 'cancelled') {
    if (run.kind === 'cache_test' || run.kind === 'speed_test')
      return 'Load test cancelled'
    if (run.kind === 'cache_compare') return 'Readyset comparison cancelled'
    if (run.kind === 'analyze') return 'Analysis cancelled'
    if (isHealthCheckKind(run.kind)) return 'Health check cancelled'
  }
  if (run.status !== 'done')
    return run.message || 'Background task did not finish'
  if (run.kind === 'cache_compare')
    return run.message || 'Readyset comparison complete'
  if (run.kind === 'analyze') return 'Analysis complete'
  return doneLabel(run)
}

export function isTerminal(run: BackgroundRunState): boolean {
  return ['done', 'partial', 'failed', 'cancelled', 'interrupted'].includes(
    run.status
  )
}

export function isActive(run: BackgroundRunState): boolean {
  return (
    !isQueuedRun(run) &&
    (run.status === 'running' ||
      run.status === 'reconnecting' ||
      run.status === 'stopping')
  )
}

export type RunSeverity = 'error' | 'warning' | 'running' | 'neutral'

export function severityFor(run: BackgroundRunState): RunSeverity {
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

/** Loudest first, then newest: the order every host lists jobs in. */
export function orderRuns(runs: BackgroundRunState[]): BackgroundRunState[] {
  return runs
    .map((run, index) => ({ run, index }))
    .sort(
      (a, b) =>
        SEVERITY_PRIORITY[severityFor(a.run)] -
          SEVERITY_PRIORITY[severityFor(b.run)] || b.index - a.index
    )
    .map(({ run }) => run)
}

export function needsKeyDetail(): string {
  return 'Sign in to Readyset or add your own Anthropic key.'
}

export function detailFor(run: BackgroundRunState): string {
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
export function isOpenable(run: BackgroundRunState): boolean {
  return (
    run.kind === 'cache_test' ||
    run.kind === 'speed_test' ||
    run.kind === 'load_test' ||
    run.kind === 'cache_compare' ||
    run.kind === 'analyze' ||
    isHealthCheckKind(run.kind)
  )
}

function summaryLine(runs: BackgroundRunState[]): string {
  const queuedCount = runs.filter(isQueuedRun).length
  const activeCount = runs.filter(isActive).length
  const errorCount = runs.filter((run) => severityFor(run) === 'error').length
  const warningCount = runs.filter(
    (run) => severityFor(run) === 'warning'
  ).length
  if (errorCount > 0)
    return `${errorCount} ${errorCount === 1 ? 'job has' : 'jobs have'} failed`
  if (warningCount > 0)
    return `${warningCount} ${warningCount === 1 ? 'job needs' : 'jobs need'} attention`
  if (activeCount > 0 && queuedCount > 0)
    return `${activeCount} running · ${queuedCount} queued`
  if (activeCount > 0)
    return `${activeCount} ${activeCount === 1 ? 'job' : 'jobs'} running`
  if (queuedCount > 0)
    return `${queuedCount} ${queuedCount === 1 ? 'job' : 'jobs'} queued`
  return 'No jobs running'
}

function RunCard({
  run,
  onNeedsKey,
  onOpen,
  onStop,
}: {
  run: BackgroundRunState
  onNeedsKey: () => void
  onOpen: () => void
  onStop: (run: BackgroundRunState) => void
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
          {/* Stopping a run is not dismissing a finished one: it says so, and
              it asks first (B-10). */}
          <Button
            variant="negative"
            modifier="ghost"
            size="small"
            icon="close"
            iconPosition="left"
            label="Stop"
            title={`Stop ${titleFor(run)}`}
            disabled={run.status === 'stopping'}
            onClick={() => onStop(run)}
          />
        </HStack>
      </div>
    )
  }

  const succeeded = run.status === 'done'
  const partial = run.status === 'partial'
  const cancelled = run.status === 'cancelled'
  const tone = succeeded
    ? 'bg-surface-positive-soft/20'
    : partial
      ? 'bg-surface-warning-soft/20'
      : cancelled
        ? 'bg-surface-layout-2'
        : 'bg-surface-negative-soft/20'
  const detailTone = succeeded
    ? 'text-content-positive-soft'
    : partial
      ? 'text-content-warning-soft'
      : cancelled
        ? 'text-content-layout-2'
        : 'text-content-negative-soft'
  const content = (
    <HStack className="min-w-0 items-start gap-2">
      <Icon
        name={succeeded ? 'tick' : cancelled ? 'close' : 'alert'}
        label={
          succeeded
            ? 'Complete'
            : partial
              ? 'Partially complete'
              : cancelled
                ? 'Cancelled'
                : 'Background task issue'
        }
        className={`h-3.5 w-3.5 shrink-0 ${detailTone}`}
      />
      <VStack className="min-w-0 flex-1 items-start gap-0">
        <Text level="caption" className="w-full truncate text-content-layout-1">
          {titleFor(run)}
        </Text>
        <Text
          level="caption"
          className={`w-full ${succeeded ? 'truncate' : 'whitespace-normal'} ${detailTone}`}
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

/**
 * Stopping a job, as its own state.
 *
 * The dialog belongs to the host rather than to the list: a popover closes the
 * moment focus moves into a modal, and a dialog unmounted with its popover
 * would never be answered.
 */
export function useStopJobConfirm(): {
  requestStop: (run: BackgroundRunState) => void
  dialog: ReactNode
} {
  const [pending, setPending] = useState<BackgroundRunState | null>(null)

  return {
    requestStop: setPending,
    dialog: (
      <ConfirmDialog
        isOpen={pending !== null}
        onClose={() => setPending(null)}
        onConfirm={() => {
          if (pending) void cancelBackgroundRun(pending.runId)
          setPending(null)
        }}
        title="Stop this job?"
        subtitle={pending ? titleFor(pending) : undefined}
        notice={{
          accent: 'warning',
          message:
            'The job stops where it is and keeps no results. You can start it again afterwards.',
        }}
        confirmLabel="Stop job"
        confirmIcon="close"
        cancelLabel="Keep running"
      />
    ),
  }
}

/** The list every host shows: a summary line, then a card per job. */
export function BackgroundJobList({
  runs,
  heading = true,
  onNeedsKey,
  onOpen,
  onStop,
}: {
  runs: BackgroundRunState[]
  /** Omit where the host already names the list (a disclosure's own title). */
  heading?: boolean
  onNeedsKey: () => void
  onOpen: (run: BackgroundRunState) => void
  onStop: (run: BackgroundRunState) => void
}) {
  return (
    <>
      {heading ? (
        <HStack className="items-center justify-between border-b border-border-layout-1 px-4 py-3">
          <VStack className="items-start gap-0">
            <Text level="label-small" className="text-content-layout-1">
              Jobs
            </Text>
            <Text level="caption" className="text-content-layout-3">
              {summaryLine(runs)}
            </Text>
          </VStack>
          <Text level="caption" className="text-content-layout-3">
            {runs.length} total
          </Text>
        </HStack>
      ) : null}
      <VStack className="max-h-[min(32rem,calc(100vh-2rem))] items-stretch gap-2 overflow-y-auto p-2">
        {runs.map((run) => (
          <RunCard
            key={run.runId}
            run={run}
            onNeedsKey={onNeedsKey}
            onOpen={() => onOpen(run)}
            onStop={onStop}
          />
        ))}
      </VStack>
    </>
  )
}

/**
 * The job list where the sidebar cannot be reached: a modal surface takes the
 * whole viewport's pointer events, so the list travels with it (B-08).
 */
export function InlineBackgroundJobs() {
  const runs = orderRuns(useBackgroundRunsStore().filter((run) => !run.hidden))
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const close = useCallback(() => setOpen(false), [])
  const openRun = useOpenBackgroundRun(close)
  const stopJob = useStopJobConfirm()

  if (runs.length === 0) return null

  return (
    <>
      <Disclosure
        open={open}
        onOpenChange={setOpen}
        title="Jobs"
        subtitle={summaryLine(runs)}
        className="mt-2"
        panelClassName="p-0"
      >
        <BackgroundJobList
          runs={runs}
          heading={false}
          // Signing in for AI access is a page of its own; the sidebar's own
          // dialog belongs to the sidebar, not to a list borrowed by a drawer.
          onNeedsKey={() => {
            setOpen(false)
            void navigate({ to: '/configure' })
          }}
          onOpen={openRun}
          onStop={(run) => {
            setOpen(false)
            stopJob.requestStop(run)
          }}
        />
      </Disclosure>
      {stopJob.dialog}
    </>
  )
}

/**
 * Open the screen a job's result lives on. Terminal jobs are acknowledged on
 * the way, so opening one is also how it leaves the list.
 */
export function useOpenBackgroundRun(
  onOpened: () => void
): (run: BackgroundRunState) => void {
  const navigate = useNavigate()
  const { setTarget } = useTarget()

  return useCallback(
    (run: BackgroundRunState) => {
      if (isTerminal(run)) {
        if (isOpenable(run)) acknowledgeBackgroundRun(run.runId)
        else dismissBackgroundRun(run.runId)
      }
      onOpened()
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
      // The analyze drawer is URL-owned, so the job's own link reopens it --
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
    },
    [navigate, onOpened, setTarget]
  )
}
