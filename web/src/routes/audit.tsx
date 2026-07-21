import { cn } from '@rs/tailwind-base'
import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { BaseInputSelect } from '@rs/ui-new/base-input-select'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Show } from '@rs/ui-new/show'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { TargetLockNotice } from '../components'
import { useTarget } from '../hooks/useTarget'
import { useTrialSource } from '../lib/trialQueries'
import { useAnthropicValidity } from '../lib/useAnthropicValidity'
import { fetchAuditRuns, useAuditCapture, useAuditRun } from '../lib/useAudit'
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock'

const CAPTURE_DURATIONS: Array<{
  label: string
  long: string
  seconds: number
}> = [
  { label: '30s', long: '30 seconds', seconds: 30 },
  { label: '1m', long: '1 minute', seconds: 60 },
  { label: '5m', long: '5 minutes', seconds: 300 },
  { label: '15m', long: '15 minutes', seconds: 900 },
]

export const Route = createFileRoute('/audit')({
  component: AuditPage,
})

// Report / workload views live in the route-ignored `-audit-views` sibling so
// the shared `SQLDisplay` → CodeMirror import stays out of this eager route
// reference module (see that file's header + Defect D-1). AuditPage consumes
// them here; the standalone run-detail route imports them from the same sibling.
import {
  AuditReportView,
  formatDate,
  formatDuration,
  SectionCard,
  StatCard,
  WorkloadReportView,
} from './-audit-views'

// ---------------------------------------------------------------------------
// Idle-view launcher
// ---------------------------------------------------------------------------

/**
 * Pre-run status: the AI-insights dependency, surfaced up front instead of
 * after a wasted run (H-4; USE-065, USE-077). Renders the *actual* key state,
 * not a static "needs a key": a valid key reads "ready", the pre-resolve window
 * reads "Checking…", and only a missing/rejected key shows the config-needed
 * warning + Configure link. Reuses the cached `useAnthropicValidity` probe
 * (C-04) gated on key presence — no new endpoint. [C-09]
 */
function AiInsightsBadge() {
  const { anthropicRequirement, isTrialSource, trialStatus } = useTrialSource()
  const isTrialExhausted =
    isTrialSource &&
    (trialStatus?.status === 'exhausted' || trialStatus?.active === false)
  // Presence gates the probe so we never ping the provider without a key.
  const hasKey =
    (Boolean(anthropicRequirement?.satisfied) || isTrialSource) &&
    !isTrialExhausted
  const validityQuery = useAnthropicValidity(hasKey)
  const validity = validityQuery.data
  // Enabled-but-unresolved is the neutral "unknown" window, not a green claim.
  const checking = hasKey && validityQuery.isFetching && !validity

  // Valid → say so (accent positive); unknown → neutral "Checking…".
  if (validity?.valid) {
    return (
      <HStack className="gap-2 items-center rounded-xl border border-border-positive-soft bg-surface-positive-soft/30 px-3 py-1.5">
        <Icon
          name="sparkles"
          label=""
          aria-hidden="true"
          className="w-3.5 h-3.5 text-content-positive-soft shrink-0"
        />
        <Text level="caption" className="text-content-positive-soft">
          AI insights: ready
        </Text>
      </HStack>
    )
  }

  if (checking) {
    return (
      <HStack className="gap-2 items-center rounded-xl border border-border-layout-1 bg-surface-layout-2/40 px-3 py-1.5">
        <Icon
          name="sparkles"
          label=""
          aria-hidden="true"
          className="w-3.5 h-3.5 text-content-layout-3 shrink-0"
        />
        <Text level="caption" className="text-content-layout-3">
          AI insights: checking…
        </Text>
      </HStack>
    )
  }

  // Missing or rejected → the original config-needed copy + Configure link.
  return (
    <HStack className="gap-2 items-center rounded-xl border border-border-warning-soft bg-surface-warning-soft/30 px-3 py-1.5">
      <Icon
        name="sparkles"
        label=""
        aria-hidden="true"
        className="w-3.5 h-3.5 text-content-warning-soft shrink-0"
      />
      <Text level="caption" className="text-content-warning-soft">
        AI insights: needs a key
      </Text>
      <Text level="caption" className="text-content-layout-3">
        ·
      </Text>
      <Link
        to="/configure"
        className="hover:underline inline-flex items-center gap-0.5"
      >
        <Text level="caption" className="text-content-warning-soft">
          Configure
        </Text>
        <Icon
          name="chevron-right"
          label=""
          aria-hidden="true"
          className="w-3 h-3 text-content-warning-soft"
        />
      </Link>
    </HStack>
  )
}

/**
 * Data-handling disclosure, adjacent to the actions (H-1; USE-065, USE-066).
 * Corrects the discovery-era fear that audit emails the report — the web path
 * does not. (Presentational only: the queries_saved event stays untouched.)
 */
function DataHandlingNote() {
  return (
    <HStack className="gap-2 items-start">
      <Icon
        name="info"
        label=""
        aria-hidden="true"
        className="w-3.5 h-3.5 mt-0.5 text-content-layout-3 shrink-0"
      />
      <Text level="caption" className="text-content-layout-3">
        Runs locally on your machine. The report is saved here — nothing is
        emailed. Captured queries are added to your Saved Queries.
      </Text>
    </HStack>
  )
}

/**
 * One path (icon, title, meta, optional controls, action). Full-width and
 * stacked (not a side-by-side grid) so both cards read at the same width and
 * the eye travels top-to-bottom [Audit#1; ref 21.55.38, VIS-113]. The emphasized
 * card is raised via the elevation token; the quieter card recedes to
 * content-layout weight — emphasis is bought by de-emphasizing the neighbor,
 * not by adding colour (VIS-016, VIS-108/109). The CTA lives in a footer row,
 * right-aligned and `size="small"`, with any inline control (e.g. "Record for")
 * on its left [VIS-022/023 action hierarchy; §6.1 size ranks].
 */
function ModeCard({
  emphasized = false,
  icon,
  title,
  meta,
  controls,
  action,
}: {
  emphasized?: boolean
  icon: IconStrokeName
  title: string
  meta: string
  controls?: React.ReactNode
  action: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'rounded-[1.25rem] p-5 w-full',
        emphasized
          ? 'bg-surface-raised shadow-elevation-1 border border-border-primary-soft'
          : 'bg-surface-layout-1 border border-border-layout-1'
      )}
    >
      <VStack className="gap-4 items-stretch">
        {/* Identity: icon + title/meta on one line so the card reads wide. */}
        <HStack className="gap-3 items-start">
          <div
            className={cn(
              'w-10 h-10 rounded-xl flex items-center justify-center shrink-0',
              emphasized ? 'bg-surface-primary-soft' : 'bg-surface-layout-2'
            )}
          >
            <Icon
              name={icon}
              label=""
              aria-hidden="true"
              className={cn(
                'w-5 h-5',
                emphasized
                  ? 'text-content-primary-soft'
                  : 'text-content-layout-2'
              )}
            />
          </div>
          <VStack className="gap-1 items-start min-w-0">
            <Text level="subtitle-2" className="text-content-layout-1">
              {title}
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              {meta}
            </Text>
          </VStack>
        </HStack>

        {/* Action row: optional inline control at left, small CTA right. */}
        <HStack className="gap-3 items-end justify-between flex-wrap">
          <div className="min-w-0">{controls}</div>
          <div className="shrink-0">{action}</div>
        </HStack>
      </VStack>
    </div>
  )
}

/**
 * The idle-view launcher: two self-explanatory mode cards. `hero` renders the
 * polished empty-state above them (illustration + one-line job); the compact
 * form ("Run another check") sits under a report so both paths stay reachable
 * without a second wall of controls (VIS-102, VIS-011; H-2).
 */
function RunLauncher({
  hero,
  disabled,
  runLoading,
  runLabel,
  onRun,
  onCapture,
  captureDuration,
  onDurationChange,
}: {
  hero: boolean
  disabled: boolean
  runLoading: boolean
  runLabel: string
  onRun: () => void
  onCapture: () => void
  captureDuration: number
  onDurationChange: (seconds: number) => void
}) {
  const durationOptions = CAPTURE_DURATIONS.map((d) => ({
    value: String(d.seconds),
    label: d.long,
  }))

  return (
    <VStack className="gap-6 items-stretch">
      {/* Idle no longer repeats an icon + verdict sentence — that duplicated
          the page header. Only the "Run another check" label remains, and only
          after a report, so idle shows page header + the two cards [Health 1;
          VIS-011, VIS-016, USE-025]. */}
      {!hero && (
        <Text
          level="overline"
          className="text-content-layout-3 uppercase tracking-wider"
        >
          Run another check
        </Text>
      )}

      {/* Stacked full-width, equal width — not a side-by-side grid. [Audit#1] */}
      <VStack className="gap-4 items-stretch">
        {/* PRIMARY — instant snapshot */}
        <ModeCard
          emphasized
          icon="speedometer"
          title="Instant snapshot"
          meta="~10s · reads current metrics now"
          action={
            <Button
              variant="primary"
              modifier="solid"
              size="small"
              label={runLabel}
              icon="play"
              iconPosition="left"
              onClick={onRun}
              loading={runLoading}
              disabled={disabled}
            />
          }
        />

        {/* SECONDARY — live capture */}
        <ModeCard
          icon="observe"
          title="Live capture"
          meta="records real traffic, then analyzes what ran"
          controls={
            <HStack className="gap-2 items-center">
              <Text level="caption" className="text-content-layout-3 shrink-0">
                Record for
              </Text>
              <div className="w-40">
                <BaseInputSelect
                  name="capture-duration"
                  options={durationOptions}
                  value={String(captureDuration)}
                  onValueChange={(v) => onDurationChange(Number(v))}
                  disabled={disabled}
                  triggerClassName="h-9 w-full"
                />
              </div>
            </HStack>
          }
          action={
            <Button
              variant="rising"
              modifier="outline"
              size="small"
              label="Start capture"
              icon="observe"
              iconPosition="left"
              onClick={onCapture}
              disabled={disabled}
            />
          }
        />
      </VStack>

      <DataHandlingNote />
    </VStack>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function AuditPage() {
  const queryClient = useQueryClient()
  const { target } = useTarget()
  const passwordLock = useTargetPasswordLock(target)

  const {
    run,
    state: runState,
    statusMessage,
    report: liveReport,
    error: runError,
    reset,
  } = useAuditRun()

  const {
    run: runCapture,
    cancel: cancelCapture,
    reset: resetCapture,
    state: captureState,
    statusMessage: captureStatus,
    analysisWarning,
    progress: captureProgress,
    result: captureResult,
    error: captureError,
  } = useAuditCapture()

  const [captureDuration, setCaptureDuration] = useState<number>(60)

  const isRunning = runState === 'running'
  const isCapturing =
    captureState === 'capturing' || captureState === 'analyzing'
  const busy = isRunning || isCapturing
  // Past runs now open on their own route (/audit/runs/$runId); this page only
  // stages the just-run live result.
  const report = liveReport

  const { data: runsData, refetch: refetchRuns } = useQuery({
    queryKey: ['audit-runs', target],
    queryFn: () => fetchAuditRuns(target!),
    enabled: !!target,
    staleTime: 30_000,
  })
  const runs = runsData?.runs || []

  const handleRun = async () => {
    if (!target) return
    resetCapture()
    await run(target)
    queryClient.invalidateQueries({ queryKey: ['audit-runs', target] })
    refetchRuns()
  }

  const handleCapture = async () => {
    if (!target) return
    reset()
    await runCapture(target, { duration: captureDuration })
    queryClient.invalidateQueries({ queryKey: ['audit-runs', target] })
    refetchRuns()
  }

  // A capture result currently occupies the stage (fresh live capture).
  const captureComplete = captureState === 'complete' && !!captureResult
  // Something already fills the stage (live report / capture result).
  const showStageResult = !!report || captureComplete
  // Idle, pre-run: the empty-state hero + the AI-insights badge belong here.
  const isIdle = !busy && !showStageResult

  const launcherDisabled = busy || !target || passwordLock.isLocked
  const runActionLabel = report ? 'Run new audit' : 'Run audit'

  return (
    <div className="space-y-6 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start gap-4 flex-wrap">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon
                name="document-validation"
                label="Health Check"
                className="w-6 h-6 text-content-info-soft"
              />
            </div>
            <VStack className="gap-1 items-start">
              <Text
                as="h1"
                level="headline-3"
                className="text-content-layout-1"
              >
                Health Check
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Full audit of "{target}": sizing verdict, slow spots, and cache
                opportunities.
              </Text>
            </VStack>
          </HStack>
          {/* Status slot: pre-run AI-insights dependency (H-4). */}
          {isIdle && <AiInsightsBadge />}
        </HStack>
      </m.div>

      {/* Password lock */}
      {passwordLock.isLocked && (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      )}

      {/* Run progress */}
      <AnimatePresence>
        {isRunning && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full">
              <Card.Content>
                <HStack className="gap-3 items-center p-2">
                  <Spinner size="base" />
                  <Text level="body-small" className="text-content-layout-2">
                    {statusMessage || 'Auditing ' + (target ?? '') + '…'}
                  </Text>
                </HStack>
              </Card.Content>
            </Card>
          </m.div>
        )}
      </AnimatePresence>

      {/* Run error */}
      <Show when={runState === 'error' && !!runError}>
        <div className="px-5 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Error"
              className="w-4 h-4 text-content-negative-soft"
            />
            <Text level="body-small" className="text-content-negative-soft">
              {runError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Live capture panel */}
      <AnimatePresence>
        {isCapturing && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="w-full">
              <Card.Content>
                <VStack className="gap-4 items-stretch p-2">
                  <HStack className="gap-3 items-center justify-between flex-wrap">
                    <HStack className="gap-3 items-center">
                      <Spinner size="base" />
                      <Text
                        level="body-small"
                        className="text-content-layout-2"
                      >
                        {captureState === 'analyzing'
                          ? captureStatus || 'Analyzing captured workload...'
                          : captureStatus || 'Capturing live workload...'}
                      </Text>
                    </HStack>
                    <HStack className="gap-3 items-center">
                      {captureProgress && (
                        <Text
                          level="mono-small"
                          className="text-content-layout-3 tabular-nums shrink-0"
                        >
                          {Math.round(captureProgress.elapsedSeconds)}s
                          {captureProgress.totalSeconds
                            ? ` / ${captureProgress.totalSeconds}s`
                            : ''}
                        </Text>
                      )}
                      <Button
                        variant="negative"
                        modifier="outline"
                        size="small"
                        label="Cancel"
                        icon="close"
                        iconPosition="left"
                        onClick={cancelCapture}
                      />
                    </HStack>
                  </HStack>

                  {captureProgress?.totalSeconds ? (
                    <div className="h-2 w-full rounded-full bg-surface-layout-2 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-surface-primary-solid transition-[width] duration-500 ease-linear"
                        style={{
                          width: `${Math.min(
                            100,
                            (captureProgress.elapsedSeconds /
                              captureProgress.totalSeconds) *
                              100
                          )}%`,
                        }}
                      />
                    </div>
                  ) : null}

                  {captureProgress && (
                    <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
                      <StatCard
                        label="Unique Queries"
                        value={`${captureProgress.uniqueQueries}`}
                      />
                      <StatCard
                        label="Executions"
                        value={captureProgress.totalExecutions.toLocaleString()}
                      />
                      <StatCard
                        label="TPS"
                        value={captureProgress.tps.toFixed(1)}
                      />
                      <StatCard
                        label="Cache Hit"
                        value={
                          captureProgress.cacheHitRatio != null
                            ? `${captureProgress.cacheHitRatio.toFixed(1)}%`
                            : '-'
                        }
                        hint={`${captureProgress.activeConnections} conns`}
                      />
                    </div>
                  )}
                </VStack>
              </Card.Content>
            </Card>
          </m.div>
        )}
      </AnimatePresence>

      {/* Capture analysis warning (graceful degradation) */}
      <Show when={!!analysisWarning}>
        <div className="px-5 py-3 bg-surface-warning-soft/20 border border-border-warning-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Warning"
              className="w-4 h-4 text-content-warning-soft"
            />
            <Text level="body-small" className="text-content-warning-soft">
              {analysisWarning}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Capture error */}
      <Show when={captureState === 'error' && !!captureError}>
        <div className="px-5 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Error"
              className="w-4 h-4 text-content-negative-soft"
            />
            <Text level="body-small" className="text-content-negative-soft">
              {captureError}
            </Text>
          </HStack>
        </div>
      </Show>

      {/* Idle empty-state hero + two mode cards (the launcher) */}
      {isIdle && (
        <m.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <RunLauncher
            hero
            disabled={launcherDisabled}
            runLoading={isRunning}
            runLabel={runActionLabel}
            onRun={handleRun}
            onCapture={handleCapture}
            captureDuration={captureDuration}
            onDurationChange={setCaptureDuration}
          />
        </m.div>
      )}

      {/* Live capture result */}
      {captureComplete && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <VStack className="gap-3 items-stretch">
            <HStack className="gap-2 items-center">
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                Latest capture: {captureResult!.runId}
              </Text>
            </HStack>
            <WorkloadReportView
              summary={captureResult!.summary}
              analysis={captureResult!.analysis}
              queries={captureResult!.summary?.queries || []}
              durationSeconds={
                captureResult!.summary?.duration_seconds ?? captureDuration
              }
            />
          </VStack>
        </m.div>
      )}

      {/* Report — the just-run live audit result */}
      {report && !isRunning && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <VStack className="gap-3 items-stretch">
            <HStack className="justify-between items-center">
              <HStack className="gap-2 items-center">
                <Text
                  level="overline"
                  className="text-content-layout-3 uppercase tracking-wider"
                >
                  Latest audit
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Saved · {formatDate(report.audited_at)}
                </Text>
              </HStack>
            </HStack>
            <AuditReportView report={report} />
          </VStack>
        </m.div>
      )}

      {/* Run another check — keeps both paths reachable after a result. */}
      {!busy && showStageResult && (
        <RunLauncher
          hero={false}
          disabled={launcherDisabled}
          runLoading={isRunning}
          runLabel={runActionLabel}
          onRun={handleRun}
          onCapture={handleCapture}
          captureDuration={captureDuration}
          onDurationChange={setCaptureDuration}
        />
      )}

      {/* Past runs — hidden entirely until at least one run exists (VIS-103). */}
      <Show when={runs.length > 0}>
        <SectionCard icon="folder-file" title={`Past Runs (${runs.length})`}>
          <div className="divide-y divide-border-layout-1">
            {runs.map((summary) => {
              const isCapture = (summary.duration_seconds ?? 0) > 0
              const runLabel = isCapture ? 'Workload capture' : 'Quick audit'
              return (
                <Link
                  key={summary.run_id}
                  to="/audit/runs/$runId"
                  params={{ runId: summary.run_id }}
                  className="group block w-full text-left px-5 py-3 hover:bg-surface-layout-2/50 transition-colors cursor-pointer"
                >
                  <HStack className="justify-between items-center gap-4">
                    <VStack className="gap-0.5 items-start min-w-0">
                      <HStack className="gap-2 items-baseline min-w-0">
                        <Text
                          level="label-medium"
                          className="text-content-layout-1 shrink-0"
                        >
                          {runLabel}
                        </Text>
                        <Text
                          level="caption"
                          className="text-content-layout-3 truncate"
                        >
                          {formatDate(summary.started_at)}
                        </Text>
                      </HStack>
                      <Text
                        level="mono-small"
                        className="text-content-layout-3 truncate"
                      >
                        {summary.run_id}
                      </Text>
                    </VStack>
                    <HStack className="gap-2 items-center shrink-0">
                      {isCapture && (
                        <Tag
                          size="small"
                          variant="warning"
                          modifier="ghost"
                          label={formatDuration(summary.duration_seconds)}
                        />
                      )}
                      {summary.has_analysis && (
                        <Tag
                          size="small"
                          variant="positive"
                          modifier="ghost"
                          label="Analyzed"
                        />
                      )}
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={summary.source || 'audit'}
                      />
                      <Icon
                        name="chevron-right"
                        label="Open run"
                        className="w-4 h-4 text-content-layout-3 opacity-0 group-hover:opacity-100 transition-opacity"
                      />
                    </HStack>
                  </HStack>
                </Link>
              )
            })}
          </div>
        </SectionCard>
      </Show>
    </div>
  )
}
