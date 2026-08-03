/**
 * Health Check route. The report bodies live under `components/audit/report/`
 * so this eager route reference module never pulls the CodeMirror SQL stack
 * into the entry chunk; `AuditPage` is local, so the code-splitter relocates it
 * (and its report imports) into the lazy route chunk.
 */

import type { IconStrokeName } from '@rs/ui-icons/icon-name'
import { Button } from '@rs/ui-new/button'
import { Card } from '@rs/ui-new/card'
import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { toast } from '@rs/ui-new/use-toast'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { EnvSecretsDialog } from '../components'
import { FleetRunSection } from '../components/audit/FleetRunSection'
import { PreflightChecklist } from '../components/audit/PreflightChecklist'
import { RunHistory } from '../components/audit/RunHistory'
import { RunLauncher } from '../components/audit/RunLauncher'
import { RunProgress } from '../components/audit/RunProgress'
import { ScopeSelector } from '../components/audit/ScopeSelector'
import { RoutableNotice } from '../components/RoutableNotice'
import { TrialRegistrationDialog } from '../components/TrialRegistrationDialog'
import { useTarget } from '../hooks/useTarget'
import { buildHistory, type HistoryEntry } from '../lib/auditHistory'
import {
  type AuditPreflightResult,
  checkAuditPreflight,
  isAuditPreflightBlocked,
} from '../lib/auditPreflight'
import {
  buildTargetSelectionRequest,
  parseAuditSearch,
  selectionFromAuditSearch,
} from '../lib/auditScope'
import {
  type ActiveAuditSession,
  clearCompletedAudit,
  setAuditRunViewVisible,
  useAuditPresentation,
  useAuditSession,
} from '../lib/auditSession'
import {
  classifyError,
  isTrialExhaustedError,
  recoveryFor,
  sanitizeWebError,
  TRIAL_EXHAUSTED_MESSAGE,
} from '../lib/errorContract'
import {
  invalidateAiGateQueries,
  invalidateTrialRelatedQueries,
} from '../lib/trialQueries'
import { useAiGate } from '../lib/useAiGate'
import { fetchAuditRuns, useAuditCapture } from '../lib/useAudit'
import { useEnvRequirements } from '../lib/useEnvRequirements'
import {
  fetchFleetSnapshots,
  fetchFleetAwsStatus,
  fetchFleetTargets,
  useFleetAudit,
  useFleetStatus,
} from '../lib/useFleet'

export const Route = createFileRoute('/audit')({
  component: AuditPage,
  // Parse-only: never throw here — /audit must render for any search params.
  validateSearch: parseAuditSearch,
})

/**
 * Owns the half-second capture clock so the ticking elapsed time re-renders
 * only the progress readout. Mounted for capture sessions only; a fleet run
 * keeps its own timer inside `FleetRunSection`.
 */
function CaptureElapsed({
  session,
  phase,
  statusMessage,
  progressElapsedSeconds,
}: {
  session: ActiveAuditSession
  phase: string | undefined
  statusMessage: string | undefined
  progressElapsedSeconds: number | undefined
}) {
  const [clock, setClock] = useState(Date.now())
  useEffect(() => {
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [session.id])
  const elapsedSeconds = Math.min(
    session.durationSeconds || Number.POSITIVE_INFINITY,
    Math.max(progressElapsedSeconds ?? 0, (clock - session.startedAt) / 1000)
  )
  return (
    <RunProgress
      phase={phase}
      statusMessage={statusMessage}
      durationSeconds={session.durationSeconds}
      elapsedSeconds={elapsedSeconds}
    />
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function AuditPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate({ from: '/audit' })
  const search = Route.useSearch()
  const { target: appTarget } = useTarget()
  const aiGate = useAiGate()
  const activeSession = useAuditSession()
  const auditPresentation = useAuditPresentation()

  // Inventory feeding the always-visible target picker.
  const { data: inventory } = useQuery({
    queryKey: ['fleet-targets'],
    queryFn: () => fetchFleetTargets(),
    staleTime: 30_000,
  })
  const members = useMemo(() => inventory?.members ?? [], [inventory])
  const [selectedTargets, setSelectedTargets] = useState<string[]>([])
  const initializedSelection = useRef('')
  useEffect(() => {
    if (members.length === 0) return
    const key = `${search.scope ?? ''}|${search.group ?? ''}|${
      search.target ?? ''
    }|${search.targets ?? ''}|${appTarget ?? ''}`
    if (initializedSelection.current === key) return
    initializedSelection.current = key
    setSelectedTargets(selectionFromAuditSearch(search, members, appTarget))
  }, [appTarget, members, search])

  const singleTarget = selectedTargets.length === 1 ? selectedTargets[0] : null
  const { data: envRequirements } = useEnvRequirements()

  // Connectivity feeds healthy-first ordering and the unavailable disclosure.
  const { check: checkConnectivity, results: connectivity } = useFleetStatus()
  const didConnectivityCheck = useRef(false)
  useEffect(() => {
    if (didConnectivityCheck.current) return
    if (members.length === 0) return
    didConnectivityCheck.current = true
    void checkConnectivity()
  }, [members.length, checkConnectivity])

  const {
    run: runCapture,
    reset: resetCapture,
    state: captureState,
    statusMessage: captureStatus,
    phase: capturePhase,
    analysisWarning,
    readysetNotice,
    progress: captureProgress,
    error: captureError,
  } = useAuditCapture()

  const fleetAudit = useFleetAudit()

  const [captureDuration, setCaptureDuration] = useState<number>(60)
  const [view, setView] = useState<'run' | 'history'>(
    search.tab === 'history' ? 'history' : 'run'
  )

  // Per-target preflight is shown before every run and cached for one minute.
  const [preflight, setPreflight] = useState<AuditPreflightResult | null>(null)
  const [requirementsBusy, setRequirementsBusy] = useState(false)
  const [awsProfile, setAwsProfile] = useState('')
  const [runPasswordTarget, setRunPasswordTarget] = useState<string | null>(
    null
  )
  const [showRunTrialDialog, setShowRunTrialDialog] = useState(false)

  useEffect(() => {
    setPreflight(null)
  }, [selectedTargets.join('\u0000')])

  const selectedMembers = useMemo(
    () => members.filter((member) => selectedTargets.includes(member.name)),
    [members, selectedTargets]
  )
  // Provider-discovered targets (Supabase, Neon, DigitalOcean) also carry a
  // region and instance_class, so the AWS gate keys off the real AWS signals
  // only and never fires for a provider target.
  const awsPreflightRequired = selectedMembers.some(
    (member) =>
      !(member.tags ?? []).some((tag) => tag.startsWith('provider:')) &&
      (member.instance_class_source === 'aws' ||
        (member.tags ?? []).some((tag) => tag.startsWith('aws-account:')) ||
        (member.host ?? '').endsWith('.rds.amazonaws.com'))
  )

  const checkRequirements = async (
    force = false
  ): Promise<AuditPreflightResult | null> => {
    if (selectedTargets.length === 0) return null
    setRequirementsBusy(true)
    const targetAccounts: Record<string, string> = {}
    for (const member of selectedMembers) {
      const accountTag = (member.tags ?? []).find((tag) =>
        tag.startsWith('aws-account:')
      )
      if (accountTag) {
        targetAccounts[member.name] = accountTag.slice('aws-account:'.length)
      }
    }
    // The checklist reports the AI gate next to the database and Docker
    // probes, so a re-check re-resolves the key and trial balance too.
    const [result] = await Promise.all([
      checkAuditPreflight(selectedTargets, {
        force,
        awsRequired: awsPreflightRequired,
        awsProfile: awsProfile || undefined,
        awsFetcher: fetchFleetAwsStatus,
        targetAccounts,
      }),
      invalidateAiGateQueries(queryClient),
    ])
    if (!awsProfile && result.aws.status?.active_profile) {
      setAwsProfile(result.aws.status.active_profile)
    }
    setPreflight(result)
    setRequirementsBusy(false)
    return result
  }

  const isCapturing =
    captureState === 'capturing' || captureState === 'analyzing'
  const isFleetRunning = fleetAudit.state === 'running'
  const busy = !!activeSession || isCapturing || isFleetRunning
  useEffect(() => {
    setAuditRunViewVisible(view === 'run')
    return () => setAuditRunViewVisible(false)
  }, [view])
  useEffect(() => {
    if (view !== 'history') return
    const saved = Number(
      sessionStorage.getItem('rdst-audit-history-scroll') || 0
    )
    requestAnimationFrame(() => window.scrollTo({ top: saved }))
  }, [view])

  // Unified history: single-target runs + fleet snapshots.
  const { data: runsData } = useQuery({
    queryKey: ['audit-runs'],
    queryFn: () => fetchAuditRuns(),
    staleTime: 30_000,
  })
  const { data: snapshotsData } = useQuery({
    queryKey: ['fleet-snapshots'],
    queryFn: fetchFleetSnapshots,
    staleTime: 30_000,
  })
  const history = useMemo(
    () => buildHistory(runsData?.runs ?? [], snapshotsData?.snapshots ?? []),
    [runsData, snapshotsData]
  )

  const invalidateHistory = () => {
    queryClient.invalidateQueries({ queryKey: ['audit-runs'] })
    queryClient.invalidateQueries({ queryKey: ['fleet-snapshots'] })
  }

  const runFleetSelection = async (durationSeconds?: number) => {
    resetCapture()
    setView('run')
    const request = buildTargetSelectionRequest(
      selectedTargets,
      durationSeconds
    )
    await fleetAudit.runAudit(request)
    invalidateHistory()
  }

  const handleRun = async () => {
    if (activeSession || selectedTargets.length === 0) return
    const checked = await checkRequirements()
    if (
      !checked ||
      isAuditPreflightBlocked(checked, {
        requireQueryStats: captureDuration > 0,
      })
    ) {
      // Never swallow the click: the checklist above shows exactly which
      // item is blocking.
      toast({
        title: "Health check can't start yet",
        description:
          'Resolve the requirement checklist items above, then run again.',
        variant: 'warning',
      })
      return
    }
    if (selectedTargets.length > 1) {
      await runFleetSelection(captureDuration || undefined)
      return
    }
    if (!singleTarget) return
    fleetAudit.reset()
    setView('run')
    await runCapture(singleTarget, { duration: captureDuration })
    invalidateHistory()
  }

  const handleRetryTarget = (name: string) => {
    void navigate({
      replace: true,
      search: { scope: undefined, group: undefined, target: name },
    })
    setSelectedTargets([name])
    fleetAudit.reset()
    setView('run')
    void runCapture(name, { duration: captureDuration })
  }

  const handleOpenHistory = async (entry: HistoryEntry) => {
    sessionStorage.setItem('rdst-audit-history-scroll', String(window.scrollY))
    await navigate({ to: '/audit/runs/$runId', params: { runId: entry.id } })
  }

  const handledCompletion = useRef<number | null>(null)
  useEffect(() => {
    const completed = auditPresentation.completed
    if (
      view !== 'run' ||
      !completed ||
      handledCompletion.current === completed.id
    )
      return
    handledCompletion.current = completed.id
    clearCompletedAudit(completed.id)
    void navigate({
      to: '/audit/runs/$runId',
      params: { runId: completed.runId },
    })
  }, [auditPresentation.completed?.id, view])

  const handledViewRequest = useRef(0)
  useEffect(() => {
    if (auditPresentation.viewRequestId <= handledViewRequest.current) return
    handledViewRequest.current = auditPresentation.viewRequestId
    if (activeSession) {
      setView('run')
      return
    }
    const completed = auditPresentation.completed
    if (!completed) return
    handledCompletion.current = completed.id
    clearCompletedAudit(completed.id)
    void navigate({
      to: '/audit/runs/$runId',
      params: { runId: completed.runId },
    })
  }, [auditPresentation.viewRequestId])

  useEffect(() => {
    if (view !== 'run' || activeSession) return
    if (captureState === 'complete' || captureState === 'error') {
      resetCapture()
    }
    if (fleetAudit.state === 'complete' || fleetAudit.state === 'error') {
      fleetAudit.reset()
    }
  }, [view])

  const showFleetRun =
    fleetAudit.state === 'running' || fleetAudit.state === 'error'

  const aiBlocked = aiGate.status === 'blocked' || aiGate.status === 'checking'
  // Selecting a target never blocks on a missing password. A locked target is
  // surfaced at preflight (the checklist's inline "Set password"), not on the
  // pick, so choosing targets stays friction-free.
  const selectionReady = selectedTargets.length > 0
  const preflightBlocksLaunch =
    preflight !== null &&
    isAuditPreflightBlocked(preflight, {
      requireQueryStats: captureDuration > 0,
    })
  const launcherDisabled =
    busy ||
    requirementsBusy ||
    aiBlocked ||
    !selectionReady ||
    preflightBlocksLaunch
  const fleetTargetNames =
    activeSession?.kind === 'fleet' && activeSession.targetNames.length > 0
      ? activeSession.targetNames
      : Object.keys(fleetAudit.targets).length > 0
        ? Object.keys(fleetAudit.targets)
        : selectedTargets
  const fleetScopeLabel =
    fleetTargetNames.length === 1
      ? `Running on ${fleetTargetNames[0]}`
      : `Running on ${fleetTargetNames.length} targets: ${fleetTargetNames.join(', ')}`

  const scopeControl = (
    <VStack className="gap-2 items-stretch">
      <ScopeSelector
        members={members}
        connectivity={connectivity}
        selection={selectedTargets}
        onSelectionChange={setSelectedTargets}
        disabled={busy}
        collapsed={busy}
      />
      <HStack className="justify-end items-center gap-1">
        <Text level="caption" className="text-content-layout-3">
          Not seeing a target you expected?
        </Text>
        <Link to="/configure" hash="connections">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Manage targets in Settings"
          />
        </Link>
      </HStack>
    </VStack>
  )

  const requirementsNotice = preflight ? (
    <PreflightChecklist
      result={preflight}
      busy={requirementsBusy}
      onRecheck={() => checkRequirements(true)}
      liveCapture={captureDuration > 0}
      aiGate={aiGate}
      members={selectedMembers}
      passwordRequirements={
        envRequirements?.requirements.filter(
          (requirement) => requirement.kind === 'target_password'
        ) ?? []
      }
      anthropicRequirement={envRequirements?.requirements.find(
        (requirement) => requirement.kind === 'anthropic_api_key'
      )}
      keyringAvailable={envRequirements?.keyring_available ?? false}
      awsProfile={awsProfile}
      onAwsProfileChange={(profile) => {
        setAwsProfile(profile)
        // Keep the checklist mounted for its controlled profile picker, but
        // discard the old account identity immediately. The next explicit
        // check repopulates this block from the selected profile's STS result.
        setPreflight((current) =>
          current
            ? {
                ...current,
                aws: { required: current.aws.required },
              }
            : current
        )
      }}
    />
  ) : null

  const launcher = (
    <RunLauncher
      scopeControl={scopeControl}
      requirementsNotice={requirementsNotice}
      showRequirementsButton={selectedTargets.length > 0}
      requirementsBusy={requirementsBusy}
      onCheckRequirements={() => void checkRequirements(true)}
      disabled={launcherDisabled}
      active={busy}
      onRun={() => void handleRun()}
      captureDuration={captureDuration}
      onDurationChange={setCaptureDuration}
      selectedTargets={selectedTargets}
      runSolid={
        !preflight?.aws.required || !!preflight.aws.status?.has_credentials
      }
    />
  )

  const activePhase = capturePhase ?? activeSession?.phase

  const captureErrorClass = captureError
    ? classifyError({ code: '', message: captureError })
    : 'database'
  const readysetSkippedNoQueries =
    !!readysetNotice &&
    /no (?:live |captured |capture )?queries/i.test(readysetNotice)

  const recoveryAction = (errorClass: ReturnType<typeof classifyError>) => {
    if (errorClass === 'database') {
      return {
        label: 'Check connectivity in Settings',
        icon: 'chevron-right' as IconStrokeName,
        onClick: () => void navigate({ to: '/configure', hash: 'connections' }),
      }
    }
    const recovery = recoveryFor(errorClass)
    return recovery
      ? {
          label: recovery.label,
          onClick: () => void navigate({ to: recovery.to }),
        }
      : undefined
  }

  return (
    <div className="space-y-6 w-full">
      <Text as="h1" level="headline-3" className="text-content-layout-1">
        Health Check
      </Text>

      <div
        role="tablist"
        aria-label="Health Check sections"
        className="flex gap-1 border-b border-border-layout-1"
      >
        {(['run', 'history'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={view === tab}
            onClick={() => setView(tab)}
            className={`px-4 py-2 text-sm capitalize cursor-pointer border-b-2 ${
              view === tab
                ? 'border-border-primary-soft text-content-primary-soft'
                : 'border-transparent text-content-layout-3 hover:text-content-layout-2'
            }`}
          >
            {tab === 'history' ? 'Reports' : 'Run'}
          </button>
        ))}
      </div>

      {view === 'run' && launcher}

      {view === 'run' && activeSession?.kind !== 'fleet' && activeSession && (
        <VStack className="gap-3 items-stretch">
          <div
            className="truncate"
            title={activeSession.targetNames.join(', ')}
          >
            <Text level="label-small" className="text-content-layout-1">
              {activeSession.targetNames.length === 1
                ? `Running on ${activeSession.targetNames[0]}`
                : `Running on ${activeSession.targetNames.length} targets: ${activeSession.targetNames.join(', ')}`}
            </Text>
          </div>
          <CaptureElapsed
            session={activeSession}
            phase={activePhase}
            statusMessage={captureStatus ?? activeSession.statusMessage}
            progressElapsedSeconds={captureProgress?.elapsedSeconds}
          />
        </VStack>
      )}

      {/* Capture analysis warning (graceful degradation) */}
      <Show when={view === 'run' && !!analysisWarning}>
        {isTrialExhaustedError(analysisWarning) ? (
          <RoutableNotice
            kind="trial-exhausted"
            message={TRIAL_EXHAUSTED_MESSAGE}
            onRetry={() => setShowRunTrialDialog(true)}
            retryLabel="Start trial"
          />
        ) : (
          <InlineNotice
            errorClass="provider"
            title="Analysis skipped"
            message={analysisWarning ?? ''}
            trustworthy="The captured queries and totals below are complete and unaffected."
          />
        )}
      </Show>

      <Show when={view === 'run' && !!readysetNotice}>
        {readysetSkippedNoQueries ? (
          <Card className="w-full">
            <Card.Content>
              <HStack className="gap-3 items-start">
                <Icon
                  name="info"
                  label="Information"
                  className="w-4 h-4 mt-0.5 text-content-layout-3 shrink-0"
                />
                <VStack className="gap-1 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    Readyset benchmark not needed
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    No live queries were captured. The database health check and
                    saved results are complete.
                  </Text>
                </VStack>
              </HStack>
            </Card.Content>
          </Card>
        ) : (
          <InlineNotice
            errorClass="valid-negative"
            title="Readyset benchmark skipped"
            message={readysetNotice ?? ''}
            trustworthy="The database capture and saved query data are complete."
          />
        )}
      </Show>

      {/* Capture error */}
      <Show when={view === 'run' && captureState === 'error' && !!captureError}>
        {isTrialExhaustedError(captureError) ? (
          <RoutableNotice
            kind="trial-exhausted"
            message={TRIAL_EXHAUSTED_MESSAGE}
            onRetry={() => setShowRunTrialDialog(true)}
            retryLabel="Start trial"
          />
        ) : (
          <ErrorState
            errorClass={captureErrorClass}
            title="Capture failed"
            message={sanitizeWebError(
              captureError,
              'The capture could not be completed.'
            )}
            trustworthy="No capture was saved; earlier reports are unaffected."
            action={recoveryAction(captureErrorClass)}
            onRetry={() => void handleRun()}
            retryLabel="Retry"
          />
        )}
      </Show>

      {/* Fleet/group/multi run progress + results */}
      <Show when={view === 'run' && showFleetRun}>
        <FleetRunSection
          scopeLabel={fleetScopeLabel}
          state={fleetAudit.state}
          phase={fleetAudit.phase}
          targets={fleetAudit.targets}
          statusMessage={fleetAudit.statusMessage}
          summary={fleetAudit.summary}
          snapshotId={fleetAudit.snapshotId}
          error={fleetAudit.error}
          errorCode={fleetAudit.errorCode}
          onRetryTarget={handleRetryTarget}
          onSetPassword={setRunPasswordTarget}
          onAdjustTargets={() => setView('run')}
          captureDuration={captureDuration}
        />
      </Show>

      <EnvSecretsDialog
        isOpen={runPasswordTarget !== null}
        onClose={() => setRunPasswordTarget(null)}
        requirements={
          envRequirements?.requirements.filter(
            (requirement) =>
              requirement.kind === 'target_password' &&
              requirement.target === runPasswordTarget
          ) ?? []
        }
        keyringAvailable={envRequirements?.keyring_available ?? false}
        onSuccess={() => {
          setRunPasswordTarget(null)
          void queryClient.invalidateQueries({
            queryKey: ['env-requirements'],
          })
          void checkRequirements(true)
        }}
      />
      <TrialRegistrationDialog
        isOpen={showRunTrialDialog}
        onClose={() => setShowRunTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient)
          setShowRunTrialDialog(false)
        }}
      />

      {view === 'history' && (
        <div id="history">
          <RunHistory
            entries={history}
            activeId={null}
            loadingId={null}
            onOpen={(entry) => void handleOpenHistory(entry)}
          />
        </div>
      )}
    </div>
  )
}
