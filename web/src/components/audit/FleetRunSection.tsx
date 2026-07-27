import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Card } from '@rs/ui-new/card'
import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Progress } from '@rs/ui-new/progress'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { VERDICT_LABELS } from '../../lib/auditReportFormat'
import { isTargetCapError } from '../../lib/auditScope'
import {
  classifyError,
  isTrialExhaustedError,
  sanitizeWebError,
  TRIAL_EXHAUSTED_MESSAGE,
} from '../../lib/errorContract'
import { formatSecondsClock } from '../../lib/formatters'
import { invalidateTrialRelatedQueries } from '../../lib/trialQueries'
import type {
  FleetAuditSummary,
  FleetAuditTargetState,
} from '../../lib/useFleet'
import type { FleetStreamState } from '../../types/fleet'
import { RoutableNotice } from '../RoutableNotice'
import { TrialRegistrationDialog } from '../TrialRegistrationDialog'
import { ActivityPulse } from './ActivityPulse'

function captureTiming(
  state: FleetAuditTargetState,
  clock: number,
  captureDuration: number
): { elapsed: number; total: number; percent: number } {
  const total = state.captureTotalSeconds ?? captureDuration
  const localElapsed = state.captureStartedAt
    ? (clock - state.captureStartedAt) / 1000
    : 0
  const complete = ['capture_complete', 'readyset'].includes(state.phase ?? '')
  const elapsed =
    complete || state.status === 'done'
      ? total
      : Math.max(state.captureElapsedSeconds ?? 0, localElapsed)
  return {
    elapsed,
    total,
    percent: total > 0 ? Math.min(100, (elapsed / total) * 100) : 0,
  }
}

function targetProgress(
  state: FleetAuditTargetState,
  clock: number,
  captureDuration: number
): number {
  // A target finishing is not the end of a fleet run: Readyset benchmarks are
  // sequential, then fleet insights and report assembly still remain.
  if (state.status === 'done' || state.status === 'error') return 82
  if (state.status === 'pending') return 0
  if (captureDuration <= 0) {
    if (state.phase === 'collect') return 8
    if (state.phase === 'readyset') return 65
    return 45
  }
  const capture = captureTiming(state, clock, captureDuration)
  if (state.phase === 'collect') return 4
  if (state.phase === 'capture') return 6 + capture.percent * 0.4
  if (state.phase === 'analysis') return 48
  if (state.phase === 'capture_complete') return 52
  if (state.phase === 'readyset') return 68
  return 3
}

function SectionCard({
  icon,
  title,
  actions,
  children,
}: {
  icon: IconStrokeName
  title: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="justify-between items-center">
            <HStack className="gap-2 items-center">
              <Icon
                name={icon}
                label={title}
                className="w-4 h-4 text-content-layout-3"
              />
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                {title}
              </Text>
            </HStack>
            {actions}
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  )
}

/**
 * One per-target row of a fleet/group/multi run: live status while running,
 * sizing verdict + cache score when done. Failures render as a full-width
 * inline notice below (the run continues for the other targets).
 */
function TargetRow({
  name,
  state,
  captureDuration,
  clock,
  onRetryTarget,
  onSetPassword,
  onTrialExhausted,
}: {
  name: string
  state: FleetAuditTargetState
  captureDuration: number
  clock: number
  onRetryTarget: (name: string) => void
  onSetPassword: (name: string) => void
  onTrialExhausted: () => void
}) {
  const navigate = useNavigate()
  const capture = captureTiming(state, clock, captureDuration)
  const progress = targetProgress(state, clock, captureDuration)
  const noQuerySkip =
    !!state.notice &&
    /no (?:live |captured |capture )?queries/i.test(state.notice)
  const passwordRequired =
    state.status === 'error' && /password|locked/i.test(state.error ?? '')
  const trialExhausted =
    state.status === 'error' && isTrialExhaustedError(state.error)
  const activityLabel = (() => {
    if (state.status === 'pending') return 'Waiting for the run to start'
    if (state.status === 'error') return 'Health check failed'
    if (state.status === 'done') {
      return noQuerySkip
        ? 'Complete · Readyset benchmark not needed'
        : 'Health check complete'
    }
    if (state.phase === 'collect') return 'Preparing health check'
    if (state.phase === 'capture') return 'Capturing live database activity'
    if (state.phase === 'analysis') return 'Analyzing captured workload'
    if (state.phase === 'capture_complete')
      return 'Waiting for Readyset benchmark'
    if (state.phase === 'readyset') {
      return state.benchmarkStep === 'skipped'
        ? 'No captured queries to benchmark'
        : 'Benchmarking against Readyset'
    }
    return state.statusMessage || 'Preparing health check'
  })()

  const timingLabel =
    state.status === 'running' && state.phase === 'capture' && capture.total > 0
      ? `${formatSecondsClock(capture.elapsed)} / ${formatSecondsClock(capture.total)}`
      : undefined

  const badge = (() => {
    if (state.status === 'pending')
      return { label: 'Queued', variant: 'muted' as const }
    if (state.status === 'error')
      return { label: 'Failed', variant: 'negative' as const }
    if (state.status === 'done') {
      return state.notice && !noQuerySkip
        ? { label: 'Done with notice', variant: 'warning' as const }
        : { label: 'Done', variant: 'positive' as const }
    }
    if (state.phase === 'capture')
      return { label: 'Capturing', variant: 'informative' as const }
    if (state.phase === 'capture_complete')
      return { label: 'Queued', variant: 'muted' as const }
    if (state.phase === 'readyset' && state.benchmarkStep === 'skipped')
      return { label: 'Not needed', variant: 'muted' as const }
    if (state.phase === 'readyset')
      return { label: 'Benchmarking', variant: 'informative' as const }
    return {
      label: state.phase === 'analysis' ? 'Analyzing' : 'Preparing',
      variant: 'informative' as const,
    }
  })()

  const resultVerdict =
    VERDICT_LABELS[state.verdict || 'unknown'] || VERDICT_LABELS.unknown

  return (
    <div className="px-5 py-4 hover:bg-surface-layout-2/40 transition-colors">
      <div className="grid grid-cols-1 tablet:grid-cols-[minmax(10rem,0.8fr)_minmax(16rem,1.5fr)_auto] gap-x-5 gap-y-3 items-center">
        <HStack className="gap-3 items-center min-w-0">
          <div className="h-8 w-8 rounded-lg bg-surface-layout-2 flex items-center justify-center shrink-0">
            <Icon
              name="database"
              label="Database"
              className="h-4 w-4 text-content-layout-3"
            />
          </div>
          <div className="min-w-0" title={name}>
            <Text
              level="label-small"
              className="text-content-layout-1 truncate"
            >
              {name}
            </Text>
          </div>
        </HStack>

        <VStack className="gap-2 items-stretch min-w-0">
          <HStack className="justify-between items-center gap-3 min-w-0">
            <HStack className="gap-2 items-center min-w-0">
              <Show when={state.status === 'running'}>
                <ActivityPulse label={`${activityLabel} in progress`} />
              </Show>
              <div className="min-w-0" title={activityLabel}>
                <Text
                  level="caption"
                  className="text-content-layout-2 truncate"
                >
                  {activityLabel}
                </Text>
              </div>
            </HStack>
            <Show when={!!timingLabel}>
              <Text
                level="caption"
                className="text-content-layout-3 tabular-nums shrink-0"
              >
                {timingLabel}
              </Text>
            </Show>
          </HStack>
          <Show when={state.status === 'running'}>
            <Progress value={progress} max={100} />
          </Show>
          <Show when={state.status === 'done' && !state.error}>
            <HStack className="gap-2 items-center flex-wrap">
              <Tag
                size="small"
                variant={resultVerdict.variant}
                modifier="ghost"
                label={resultVerdict.label}
              />
              <Text level="caption" className="text-content-layout-3">
                cache {state.cacheScore ?? '-'}/100
              </Text>
            </HStack>
          </Show>
        </VStack>

        <div className="tablet:justify-self-end">
          <Tag
            size="small"
            variant={badge.variant}
            modifier="ghost"
            label={badge.label}
          />
        </div>
      </div>

      <Show when={state.status === 'error'}>
        <div className="mt-3">
          {trialExhausted ? (
            <RoutableNotice
              kind="trial-exhausted"
              message={TRIAL_EXHAUSTED_MESSAGE}
              onRetry={onTrialExhausted}
              retryLabel="Start trial"
            />
          ) : (
            <InlineNotice
              errorClass={classifyError({
                code: '',
                message: state.error ?? '',
              })}
              title={
                passwordRequired
                  ? `${name} needs a password`
                  : `${name} could not be audited`
              }
              message={
                passwordRequired
                  ? `Enter the password for '${name}' again.`
                  : sanitizeWebError(
                      state.error,
                      'The target could not be reached during this run.'
                    )
              }
              trustworthy="Results for the other targets in this run are unaffected."
              action={{
                label: passwordRequired
                  ? 'Set password'
                  : 'Check connectivity in Settings',
                icon: passwordRequired ? 'key' : 'chevron-right',
                onClick: passwordRequired
                  ? () => onSetPassword(name)
                  : () => navigate({ to: '/configure', hash: 'connections' }),
              }}
              onRetry={() => onRetryTarget(name)}
              retryLabel="Retry this target"
            />
          )}
        </div>
      </Show>
      <Show when={!!state.notice}>
        <HStack className="gap-1.5 items-center mt-3">
          <Icon
            name="info"
            label="Notice"
            className={`w-3.5 h-3.5 ${
              noQuerySkip
                ? 'text-content-layout-3'
                : 'text-content-warning-soft'
            }`}
          />
          <Text
            level="caption"
            className={
              noQuerySkip
                ? 'text-content-layout-3'
                : 'text-content-warning-soft'
            }
          >
            {noQuerySkip
              ? 'Readyset was skipped because no live queries were captured.'
              : state.notice}
          </Text>
        </HStack>
      </Show>
    </div>
  )
}

function FleetInsights({ summary }: { summary: FleetAuditSummary }) {
  const insights = summary.fleet_insights
  const healthScore =
    insights &&
    typeof insights === 'object' &&
    typeof insights.health_score === 'number'
      ? (insights.health_score as number)
      : undefined
  const findingsRaw =
    insights && typeof insights === 'object'
      ? (insights.top_findings ?? insights.fleet_findings)
      : undefined
  const findings = Array.isArray(findingsRaw) ? findingsRaw : []
  const hasContent = healthScore !== undefined || findings.length > 0

  if (!hasContent) {
    return (
      <Text level="caption" className="text-content-layout-3">
        Fleet insights unavailable
      </Text>
    )
  }

  const findingTitle = (finding: unknown): string | undefined => {
    if (finding && typeof finding === 'object') {
      const record = finding as Record<string, unknown>
      const title = record.title ?? record.finding ?? record.summary
      if (typeof title === 'string') return title
    }
    if (typeof finding === 'string') return finding
    return undefined
  }

  return (
    <VStack className="gap-1.5 items-start">
      {healthScore !== undefined && (
        <HStack className="gap-2 items-center">
          <Text
            level="caption"
            className="text-content-layout-3 uppercase tracking-wider"
          >
            Fleet health
          </Text>
          <Tag
            size="small"
            variant="informative"
            modifier="ghost"
            label={`${healthScore}/100`}
          />
        </HStack>
      )}
      {findings.slice(0, 4).map((finding, index) => {
        const title = findingTitle(finding)
        if (!title) return null
        return (
          <HStack key={index} className="gap-2 items-start">
            <span className="mt-1.5 h-1 w-1 rounded-full bg-content-layout-3 shrink-0" />
            <Text level="caption" className="text-content-layout-2">
              {title}
            </Text>
          </HStack>
        )
      })}
    </VStack>
  )
}

/**
 * Progress + results for a group/multi/fleet health-check run, rendered on
 * the Health Check page. Per-target failures stay inline while the run
 * continues; the backend's target-cap rejection renders as a friendly notice
 * steering back to the target picker.
 */
export function FleetRunSection({
  scopeLabel,
  state,
  phase,
  targets,
  statusMessage,
  summary,
  snapshotId,
  error,
  errorCode,
  onRetryTarget,
  onSetPassword,
  onAdjustTargets,
  captureDuration,
}: {
  scopeLabel: string
  state: FleetStreamState
  phase: string | undefined
  targets: Record<string, FleetAuditTargetState>
  statusMessage: string | undefined
  summary: FleetAuditSummary | undefined
  snapshotId: string | undefined
  error: string | undefined
  errorCode: string | undefined
  onRetryTarget: (name: string) => void
  onSetPassword: (name: string) => void
  onAdjustTargets: () => void
  captureDuration: number
}) {
  const queryClient = useQueryClient()
  const running = state === 'running'
  const insightsActive = running && phase === 'insights'
  const targetNames = Object.keys(targets)
  const capError = isTargetCapError(errorCode)
  const [clock, setClock] = useState(Date.now())
  const [showTrialDialog, setShowTrialDialog] = useState(false)
  useEffect(() => {
    if (!running) return
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [running])
  const targetOverallProgress = targetNames.length
    ? targetNames.reduce(
        (sum, name) =>
          sum + targetProgress(targets[name], clock, captureDuration),
        0
      ) / targetNames.length
    : 0
  const overallProgress = insightsActive
    ? Math.min(96, Math.max(90, targetOverallProgress))
    : targetOverallProgress
  const capturingCount = targetNames.filter(
    (name) => targets[name].phase === 'capture'
  ).length
  const analyzingCount = targetNames.filter(
    (name) => targets[name].phase === 'analysis'
  ).length
  const preparingCount = targetNames.filter(
    (name) => targets[name].phase === 'collect'
  ).length
  const queuedCount = targetNames.filter(
    (name) => targets[name].phase === 'capture_complete'
  ).length
  const benchmarkingName = targetNames.find(
    (name) =>
      targets[name].phase === 'readyset' && targets[name].status === 'running'
  )
  const sectionStatusMessage =
    phase === 'readyset'
      ? 'Benchmarking against Readyset'
      : phase === 'insights'
        ? undefined
        : statusMessage

  return (
    <SectionCard
      icon="document-validation"
      title={`Health check — ${scopeLabel}`}
      actions={
        running && sectionStatusMessage ? (
          <HStack className="gap-2 items-center">
            <ActivityPulse />
            <Text level="caption" className="text-content-layout-3">
              {sectionStatusMessage}
            </Text>
          </HStack>
        ) : undefined
      }
    >
      <Show when={!!error && capError}>
        <div className="p-4">
          <InlineNotice
            errorClass="user-config"
            title="Too many targets for one run"
            message={
              error ??
              'This run selects more targets than a single health check supports.'
            }
            trustworthy="Nothing ran — no results were changed."
            action={{
              label: 'Adjust targets',
              icon: 'layers',
              onClick: onAdjustTargets,
            }}
          />
        </div>
      </Show>

      <Show when={!!error && !capError}>
        <div className="p-4">
          {isTrialExhaustedError({
            code: errorCode ?? '',
            message: error ?? '',
          }) ? (
            <RoutableNotice
              kind="trial-exhausted"
              message={TRIAL_EXHAUSTED_MESSAGE}
              onRetry={() => setShowTrialDialog(true)}
              retryLabel="Start trial"
            />
          ) : (
            <ErrorState
              errorClass={classifyError({
                code: errorCode ?? '',
                message: error ?? '',
              })}
              title="Fleet health check failed"
              message={sanitizeWebError(
                error,
                'The fleet health check could not be completed.'
              )}
              trustworthy="Completed target rows above are real results; earlier reports are unaffected."
            />
          )}
        </div>
      </Show>

      <Show when={targetNames.length === 0 && running}>
        <div className="p-8">
          <VStack className="gap-3 items-center">
            <ActivityPulse label="Starting health check" />
            <Text level="body-small" className="text-content-layout-3">
              Starting health check...
            </Text>
          </VStack>
        </div>
      </Show>

      <Show when={targetNames.length > 0 && running}>
        <div className="px-5 py-4 border-b border-border-layout-1 bg-surface-layout-2/30">
          <HStack className="justify-between items-start gap-4 mb-3">
            <VStack className="gap-0.5 items-start">
              <Text level="label-small" className="text-content-layout-1">
                Overall progress
              </Text>
              <Text level="caption" className="text-content-layout-3">
                {insightsActive
                  ? 'Generating combined insights; the report is still in progress'
                  : benchmarkingName
                    ? `Benchmarking ${benchmarkingName}; ${queuedCount} capture${queuedCount === 1 ? '' : 's'} queued`
                    : capturingCount > 0
                      ? `${capturingCount} target${capturingCount === 1 ? '' : 's'} capturing active queries in parallel`
                      : analyzingCount > 0
                        ? `${analyzingCount} captured workload${analyzingCount === 1 ? '' : 's'} being analyzed`
                        : preparingCount > 0
                          ? `Preparing ${preparingCount} target${preparingCount === 1 ? '' : 's'}`
                          : 'Captures complete; preparing sequential Readyset benchmarks'}
              </Text>
            </VStack>
          </HStack>
          <Progress value={overallProgress} max={100} />
        </div>
      </Show>

      <Show when={targetNames.length > 0 && !insightsActive}>
        <div className="divide-y divide-border-layout-1">
          {targetNames.map((name) => (
            <TargetRow
              key={name}
              name={name}
              state={targets[name]}
              captureDuration={captureDuration}
              clock={clock}
              onRetryTarget={onRetryTarget}
              onSetPassword={onSetPassword}
              onTrialExhausted={() => setShowTrialDialog(true)}
            />
          ))}
        </div>
      </Show>

      <Show when={insightsActive}>
        <m.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.28 }}
          className="px-5 py-8"
        >
          <VStack className="gap-3 items-center text-center">
            <div className="h-10 w-10 rounded-xl bg-surface-primary-soft flex items-center justify-center">
              <ActivityPulse label="Generating combined insights" />
            </div>
            <VStack className="gap-1 items-center">
              <Text level="label-small" className="text-content-layout-1">
                Generating combined insights
              </Text>
              <Text level="caption" className="text-content-layout-3 max-w-xl">
                Comparing the completed target results to identify fleet-wide
                patterns and priorities.
              </Text>
            </VStack>
          </VStack>
        </m.div>
      </Show>

      <Show when={state === 'complete' && !!summary}>
        <div className="px-5 py-3 border-t border-border-layout-1 bg-surface-layout-2/50">
          <VStack className="gap-2 items-stretch">
            <HStack className="gap-2 items-center flex-wrap">
              <Icon
                name="tick-double"
                label="Complete"
                className="w-4 h-4 text-content-positive-soft"
              />
              <Text level="label-small" className="text-content-layout-1">
                {summary?.successes ?? 0} audited, {summary?.failures ?? 0}{' '}
                failed
              </Text>
              <Show when={!!snapshotId}>
                <Tag
                  size="small"
                  variant="informative"
                  modifier="ghost"
                  label={`snapshot ${snapshotId}`}
                />
              </Show>
            </HStack>
            {summary && <FleetInsights summary={summary} />}
          </VStack>
        </div>
      </Show>
      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          setShowTrialDialog(false)
        }}
      />
    </SectionCard>
  )
}
