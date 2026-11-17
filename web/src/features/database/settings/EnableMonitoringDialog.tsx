// Turn on monitoring: a title, the prerequisites check, what it reads and runs, and one
// action that waits for the check to pass. Deep analysis measures real timings by default,
// with the EXPLAIN ANALYZE warning in view; the choices wait under Advanced. While a check
// fails, the dialog says plainly that monitoring can't start, with "Check again" and
// "Finish later". A database a coding agent just added lands here too, and the dialog
// follows the agent's progress until monitoring is on. The first turn-on is always the
// user's: when the agent asks for it, the dialog says it's their turn and waits.
// Monitoring's AI runs through a Readyset account or the user's own Anthropic key, so a
// signed-out user without a key signs up or adds one right here before monitoring turns on. A
// build that asks before sending anything to AI asks here, where AI starts: AI counts as allowed
// once the sign-in or key that runs it is in place, and "Continue without AI" says what stops
// before monitoring turns on without it.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useRouter } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AiDisclosure,
  AiOffComparison,
  AllowAiDialog,
} from '@/components/AiConsent'
import { AnthropicKeyDialog } from '@/components/AiProviderGate'
import { AccountLoginDialog } from '@/components/TrialRegistrationDialog'
import { selectGlobalTarget } from '@/features/database/targetSelection'
import {
  PrerequisitesChecker,
  usePrerequisites,
} from '@/features/prerequisites'
import { useSignedOut } from '@/lib/accountStatus'
import {
  type AddFlow,
  abandonAttempt,
  addFlowFor,
  endAddFlow,
  onceForAttempt,
} from '@/lib/addFlow'
import {
  type AgentEvent,
  recheckAgentTarget,
  subscribeAgentConnect,
} from '@/lib/agentConnect'
import { useAiConsent } from '@/lib/aiConsent'
import {
  type DatabaseAddStage,
  trackEvent,
  trackEventOnce,
} from '@/lib/analytics'
import {
  useMonitoringLimit,
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import { invalidateTrialRelatedQueries } from '@/lib/trialQueries'
import { userErrorClass } from '@/lib/userErrorClass'
import { DialogShell } from '../../../components/DialogShell'
import { GetStartedStrip } from '../../../components/GetStarted'
import { useSystemStatus } from '../../../lib/useSystemStatus'
import { StatsBlocked } from '../../prerequisites/StatsBlocked'
import { AgentFix } from './AgentFix'
import { ConnectionProblem } from './ConnectionProblem'
import { SettingRow } from './SettingRow'

/** Checks that are query statistics themselves: failing, monitoring has nothing to read. */
const QUERY_STATS_CHECKS = new Set([
  'pg_stat_statements_preload',
  'pg_stat_statements',
  'performance_schema',
  'statement_consumers',
  'statement_history',
])

const STAGE_OF: Record<string, DatabaseAddStage> = {
  connecting: 'monitoring_checking',
  unreachable: 'monitoring_unreachable',
  blocked: 'monitoring_blocked',
  attention: 'monitoring_ready',
  ready: 'monitoring_ready',
}

/**
 * A database the agent added: follow its progress stream while the dialog is open. Its test
 * verdicts re-run the checks here, its request to turn monitoring on hands the click to the
 * user, and monitoring on ends the attempt.
 */
function useAgentFollow(
  target: string,
  flow: AddFlow | null,
  open: boolean,
  handlers: {
    onVerdict: () => void
    onAwaitingUser: () => void
    onMonitoring: () => void
  }
) {
  const latest = useRef(handlers)
  latest.current = handlers
  const agent = flow?.via === 'agent' ? flow : null
  useEffect(() => {
    if (!open || !agent) return
    const onEvent = (event: AgentEvent) => {
      if (event.target && event.target !== target) return
      if (event.step === 'tested' || event.step === 'needs_setup') {
        onceForAttempt(agent.attempt, 'test-true', () =>
          trackEvent('database_add_test_result', {
            ok: true,
            error_class: null,
            via: 'agent',
          })
        )
        latest.current.onVerdict()
      } else if (event.step === 'error') {
        onceForAttempt(agent.attempt, 'test-false', () =>
          trackEvent('database_add_test_result', {
            ok: false,
            error_class: userErrorClass(event.message),
            via: 'agent',
          })
        )
      } else if (event.step === 'awaiting_user') {
        latest.current.onVerdict()
        latest.current.onAwaitingUser()
      } else if (event.step === 'monitoring_enabled') {
        latest.current.onMonitoring()
      }
    }
    return subscribeAgentConnect(onEvent, { since: agent.agentSince })
  }, [open, agent, target])
}

export function EnableMonitoringDialog({
  target,
  open,
  onClose,
  openDatabase = true,
}: {
  target: string
  open: boolean
  onClose: () => void
  /** Go to the database's page once monitoring is on, unless already there. */
  openDatabase?: boolean
}) {
  // Absent outside a router, as in isolated renders; then the dialog only closes.
  const router = useRouter({ warn: false })
  const settings = useMonitorSettings(target)
  // Turning on the only database is the last step of getting started.
  const onlyDatabase = useSystemStatus().data?.targets.length === 1
  // Signed out, AI runs on the user's own key, or the user signs in or adds a key here. A full
  // account or used-up AI usage holds deep analysis too.
  const limit = useMonitoringLimit(target, open).data
  const signedOut = useSignedOut()
  const needsAccount = signedOut && limit?.own_key !== true
  const deepWaits =
    limit?.target_fits === false && limit.limit != null
      ? 'once an AI slot is free'
      : limit?.allowance?.includes('hosted') && !limit.own_key
        ? 'once your AI usage is raised'
        : null
  const update = useUpdateMonitorSettings(target)
  const client = useQueryClient()
  const [automatic, setAutomatic] = useState(true)
  const [measure, setMeasure] = useState(true)
  const [signInOpen, setSignInOpen] = useState(false)
  const [keyOpen, setKeyOpen] = useState(false)
  const ai = useAiConsent()
  // "Continue without AI": the step that says what stops before monitoring turns on.
  const [withoutAi, setWithoutAi] = useState(false)
  const [allowAiOpen, setAllowAiOpen] = useState(false)
  // An earlier failed save isn't this step's failure.
  const { resetError } = ai
  useEffect(() => {
    if (open) resetError()
  }, [open, withoutAi, resetError])
  // Set when the user signs in or adds a key here: monitoring turns on once that's done.
  const turnOnAfterSetup = useRef(false)
  const initialized = useRef(false)
  const saved = settings.data
  useEffect(() => {
    if (!open) {
      initialized.current = false
      turnOnAfterSetup.current = false
      setWithoutAi(false)
      return
    }
    if (initialized.current || !saved) return
    initialized.current = true
    const configured =
      saved.configured ?? (saved.enabled_at != null || saved.enabled)
    setAutomatic(configured ? saved.deep_analysis : true)
    setMeasure(configured ? saved.analysis_mode === 'execute' : true)
  }, [open, saved])
  const unavailable = settings.data?.unavailable_reason ?? null
  // Without its statement statistics a database gives no readings, so setup comes first.
  const prerequisites = usePrerequisites(target, { enabled: open })
  const { phase } = prerequisites
  const ready = phase === 'ready' || phase === 'attention'
  // The add attempt that handed this database here, if one did.
  const flow = addFlowFor(target)
  useEffect(() => {
    if (!open) return
    const arrived = addFlowFor(target)
    if (arrived)
      onceForAttempt(arrived.attempt, 'monitoring-dialog', () =>
        trackEvent('monitoring_dialog_shown', { via: arrived.via })
      )
    else trackEvent('monitoring_dialog_shown', { via: 'page' })
  }, [open, target])
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  // Closing before monitoring is on ends an add attempt where it stood.
  const leave = useCallback(() => {
    const arrived = addFlowFor(target)
    if (arrived) {
      abandonAttempt(
        arrived.attempt,
        arrived.via,
        STAGE_OF[phaseRef.current] ?? 'monitoring_checking'
      )
      endAddFlow(target)
    }
    onClose()
  }, [target, onClose])

  const [checkingAgain, setCheckingAgain] = useState(false)
  // The agent asked to turn monitoring on; the click is the user's.
  const [agentWaiting, setAgentWaiting] = useState(false)
  useEffect(() => {
    if (!open) setAgentWaiting(false)
  }, [open])
  const checkAgain = async () => {
    setCheckingAgain(true)
    if (addFlowFor(target)?.via === 'agent')
      void recheckAgentTarget(target).catch(() => {})
    try {
      await Promise.resolve(prerequisites.recheck())
    } finally {
      setCheckingAgain(false)
    }
  }

  useAgentFollow(target, flow, open, {
    onVerdict: () => void prerequisites.recheck(),
    onAwaitingUser: () => setAgentWaiting(true),
    onMonitoring: () => {
      endAddFlow(target)
      void client.invalidateQueries({ queryKey: monitorKeys.settings(target) })
      void client.invalidateQueries({ queryKey: ['status'] })
      onClose()
    },
  })
  const blockedChecks = (prerequisites.result?.checks ?? []).filter(
    (check) => check.blocking && check.status !== 'ok'
  )
  const statsOff = blockedChecks.some((check) =>
    QUERY_STATS_CHECKS.has(check.id)
  )
  const engine = prerequisites.result?.engine
  const failedChecks =
    phase === 'unreachable'
      ? ['connection']
      : phase === 'blocked'
        ? (prerequisites.result?.checks ?? [])
            .filter((check) => check.blocking && check.status !== 'ok')
            .map((check) => check.id)
        : []
  const failedKey = failedChecks.join(',')
  useEffect(() => {
    if (!open || !failedKey) return
    for (const check of failedKey.split(','))
      trackEventOnce(`prereq-${check}`, 'monitoring_prereq_failed', { check })
  }, [open, failedKey])
  const checksPass = phase !== 'blocked' && phase !== 'unreachable'
  // Not asked about AI yet: the question comes before monitoring turns on.
  const askAi = ai.required && ai.consent === 'pending' && checksPass
  // With AI off, monitoring needs no Readyset account or key.
  const gated = needsAccount && !ai.off && checksPass
  useEffect(() => {
    if (open && gated)
      trackEvent('monitoring_signin_gate_shown', {
        via: addFlowFor(target)?.via ?? 'page',
      })
  }, [open, gated, target])
  const note =
    phase === 'blocked'
      ? `${target} is saved. Finish any time from its page.`
      : phase === 'unreachable'
        ? 'RDST needs to reach the database first.'
        : withoutAi
          ? null
          : askAi
            ? null
            : gated
              ? null
              : automatic && deepWaits
                ? `Deep analysis is on, and runs ${deepWaits}.`
                : `Deep analysis is ${automatic ? 'on' : 'off'}. Change it any time in Monitoring settings.`

  const turnOn = () =>
    update.mutate(
      {
        enabled: true,
        deep_analysis: automatic,
        analysis_mode: measure ? 'execute' : 'plans_only',
      },
      {
        onSuccess: () => {
          void client.invalidateQueries({ queryKey: ['monitoring-upgrade'] })
          if (onlyDatabase)
            trackEventOnce(
              'onboarding-done-monitor',
              'onboarding_step_completed',
              {
                step: 'monitor',
              }
            )
          endAddFlow(target)
          onClose()
          if (
            openDatabase &&
            router &&
            router.state.location.pathname !== '/database'
          ) {
            selectGlobalTarget(target)
            void router.navigate({ to: '/database' })
          }
        },
      }
    )

  // The AI question can't be skipped while its answer is still loading.
  const disabled =
    Boolean(unavailable) || !settings.data || !ready || ai.loading
  // AI counts as allowed once what runs it is in place. A choice that couldn't be saved shows
  // as ai.error; nothing turns on.
  const allowAiAndTurnOn = () =>
    void (askAi ? ai.decide('granted') : Promise.resolve()).then(
      turnOn,
      () => {}
    )
  const signIn = () => {
    turnOnAfterSetup.current = true
    setSignInOpen(true)
  }
  const addKey = () => {
    trackEvent('monitoring_signin_gate_choice', { choice: 'own_key' })
    turnOnAfterSetup.current = true
    setKeyOpen(true)
  }
  // The quieter way to run AI: a line in the body, beside the sign-up the footer offers.
  const ownKeyLine = (
    <Text
      as="p"
      level="caption"
      className="text-content-layout-3"
      data-testid="monitoring-own-key-note"
    >
      Or{' '}
      <button
        type="button"
        className="text-content-layout-1 underline underline-offset-2 disabled:opacity-50"
        disabled={disabled || ai.deciding}
        onClick={addKey}
        data-testid="monitoring-own-key"
      >
        use your own Anthropic key
      </button>
      . Slow-query alerts still need a free Readyset account.
    </Text>
  )
  const turnOnWithoutAi = () => void ai.decide('denied').then(turnOn, () => {})

  return (
    <DialogShell
      open={open}
      onClose={leave}
      title={withoutAi ? 'Monitor without AI?' : `Monitor ${target}`}
      testId="enable-monitoring"
      note={note}
      action={
        phase === 'blocked' ? (
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="primary"
              modifier="ghost"
              label="Finish later"
              onClick={leave}
              data-testid="monitoring-finish-later"
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Check again"
              loading={checkingAgain}
              onClick={() => void checkAgain()}
              data-testid="monitoring-check-again"
            />
          </div>
        ) : withoutAi ? (
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="primary"
              modifier="ghost"
              label="Back"
              onClick={() => setWithoutAi(false)}
            />
            <Button
              variant="primary"
              modifier="outline"
              label="Monitor without AI"
              loading={ai.deciding || update.isPending}
              disabled={disabled}
              onClick={turnOnWithoutAi}
              data-testid="monitoring-without-ai"
            />
          </div>
        ) : askAi ? (
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="primary"
              modifier="ghost"
              label="Continue without AI"
              disabled={ai.deciding}
              onClick={() => setWithoutAi(true)}
              data-testid="monitoring-continue-without-ai"
            />
            <Button
              variant="primary"
              modifier="solid"
              label={
                needsAccount ? 'Allow AI and sign in' : 'Allow AI and turn on'
              }
              loading={ai.deciding || update.isPending}
              disabled={disabled}
              onClick={needsAccount ? signIn : allowAiAndTurnOn}
              data-testid="monitoring-allow-ai"
            />
          </div>
        ) : gated ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="primary"
              modifier="solid"
              label="Get started with Readyset"
              loading={update.isPending && turnOnAfterSetup.current}
              disabled={disabled}
              onClick={() => {
                trackEvent('monitoring_signin_gate_choice', {
                  choice: 'sign_in',
                })
                signIn()
              }}
              data-testid="monitoring-sign-in"
            />
          </div>
        ) : (
          <Button
            variant="primary"
            modifier="solid"
            label="Turn on monitoring"
            loading={update.isPending}
            disabled={disabled}
            onClick={turnOn}
          />
        )
      }
    >
      {withoutAi ? (
        <>
          <Text
            as="p"
            level="body-small"
            className="mb-4 text-content-layout-2"
          >
            Without AI, RDST can't tell you what's slow or how to fix it.
          </Text>
          <AiOffComparison />
          {(ai.error || update.isError) && (
            <Alert
              variant="negative"
              tone="quiet"
              icon="alert"
              className="mt-3"
              label="Couldn't turn on monitoring. Try again."
            />
          )}
        </>
      ) : (
        <>
          {agentWaiting && (
            <output
              data-testid="agent-awaiting-user"
              className="mb-4 flex items-start gap-2 rounded-panel border border-border-primary-soft bg-surface-primary-soft px-3 py-2"
            >
              <Icon
                name="sparkles"
                label=""
                size="small"
                className="mt-0.5 shrink-0 text-content-primary-soft"
              />
              <span className="min-w-0">
                <Text
                  as="span"
                  level="label-medium"
                  className="block text-content-layout-1"
                >
                  Your turn: read and click{' '}
                  {askAi
                    ? needsAccount
                      ? 'Allow AI and sign in'
                      : 'Allow AI and turn on'
                    : gated
                      ? 'Get started with Readyset'
                      : 'Turn on monitoring'}
                </Text>
                <Text
                  as="span"
                  level="caption"
                  className="block text-content-layout-2"
                >
                  Your agent asked RDST to monitor {target}. Read what
                  monitoring reads and runs below, then turn it on yourself.
                </Text>
              </span>
            </output>
          )}
          {onlyDatabase && (
            <div className="mb-4">
              <GetStartedStrip step="monitor" />
            </div>
          )}
          {askAi && <AiDisclosure className="mb-4" />}
          {askAi && needsAccount && (
            <div className="-mt-2 mb-4">{ownKeyLine}</div>
          )}
          {ai.declined && checksPass && (
            <section
              aria-label="AI is off"
              data-testid="monitoring-ai-off"
              className="mb-4 flex items-center gap-3 rounded-panel border border-border-layout-2 px-4 py-3"
            >
              <Text
                as="p"
                level="caption"
                className="flex-1 text-content-layout-2"
              >
                AI is off, so RDST monitors {target} without it: charts and
                alerts for connections, stuck queries and locks.
              </Text>
              <Button
                size="small"
                modifier="outline"
                label="Turn on AI"
                onClick={() => setAllowAiOpen(true)}
              />
            </section>
          )}
          {gated && !askAi && (
            <section
              aria-label="Sign in for AI"
              data-testid="monitoring-signin-gate"
              className="mb-4 flex items-start gap-3 rounded-panel border border-border-primary-soft bg-surface-primary-soft px-4 py-3"
            >
              <Icon
                name="sparkles"
                label=""
                size="small"
                className="mt-0.5 shrink-0 text-content-primary-soft"
              />
              <div className="min-w-0">
                <Text
                  as="p"
                  level="label-medium"
                  className="text-content-layout-1"
                >
                  Monitoring's AI comes with a free Readyset account.
                </Text>
                <Text
                  as="p"
                  level="caption"
                  className="mt-1 text-content-layout-2"
                >
                  Its AI finds what's slowing each query, works out the indexes
                  worth adding and writes your nightly report. Sign up with
                  Google or email, and monitoring turns on right after.
                </Text>
                <div className="mt-2">{ownKeyLine}</div>
              </div>
            </section>
          )}
          {phase === 'blocked' && (
            <StatsBlocked
              className="mb-4"
              statsOff={statsOff}
              source={
                engine === 'mysql' ? 'performance_schema' : 'pg_stat_statements'
              }
            />
          )}
          {phase === 'unreachable' && (
            <ConnectionProblem
              target={target}
              state={prerequisites}
              onCheck={() => void checkAgain()}
            />
          )}
          {phase === 'blocked' && (
            <AgentFix target={target} onFinished={() => void checkAgain()} />
          )}
          {phase !== 'unreachable' && (
            <PrerequisitesChecker
              target={target}
              state={prerequisites}
              layout="panel"
              celebrate={false}
              recheckButton={phase !== 'blocked'}
            />
          )}
          <Text
            as="p"
            level="body-small"
            className="mt-4 text-content-layout-2"
          >
            Reads query statistics every minute and files what needs you in the
            Inbox. Deep analysis runs EXPLAIN on the busiest queries, or EXPLAIN
            ANALYZE with Measure real timings, which executes each one read-only
            with a time limit. Analyze automatically turns it off.
          </Text>
          {automatic && measure && (
            <output
              data-testid="explain-analyze-warning"
              className="mt-3 flex items-start gap-2 rounded-panel border border-border-warning-soft bg-surface-warning-soft px-3 py-2"
            >
              <Icon
                name="alert"
                label=""
                size="small"
                className="mt-0.5 shrink-0 text-content-warning-plain"
              />
              <Text as="p" level="caption" className="text-content-layout-1">
                RDST will run EXPLAIN ANALYZE on problem queries. That runs each
                one for real: read-only SELECTs only, each with a time limit.
              </Text>
            </output>
          )}
          <Disclosure title="Advanced" className="mt-3">
            <SettingRow
              title="Analyze automatically"
              line="Busiest queries first, new ones as they appear."
              control={
                <BaseInputSwitch
                  name="enable-automatic"
                  aria-label="Analyze automatically"
                  checked={automatic}
                  onCheckedChange={setAutomatic}
                />
              }
            />
            <SettingRow
              title="Measure real timings"
              line="Runs read-only SELECTs with a time limit."
              last
              control={
                <BaseInputSwitch
                  name="enable-measure"
                  aria-label="Measure real timings"
                  checked={measure}
                  onCheckedChange={setMeasure}
                />
              }
            />
          </Disclosure>
          {unavailable && (
            <Alert
              variant="warning"
              tone="quiet"
              icon="alert"
              className="mt-3"
              label={unavailable}
            />
          )}
          {askAi && ai.error && (
            <Alert
              variant="negative"
              tone="quiet"
              icon="alert"
              className="mt-3"
              label="Couldn't save your AI choice. Try again."
            />
          )}
          {update.isError && (
            <Alert
              variant="negative"
              tone="quiet"
              icon="alert"
              className="mt-3"
              label={
                update.error instanceof Error
                  ? update.error.message
                  : "Couldn't turn on monitoring"
              }
            />
          )}
        </>
      )}
      <AllowAiDialog
        isOpen={allowAiOpen}
        onClose={() => setAllowAiOpen(false)}
      />
      <AnthropicKeyDialog
        isOpen={keyOpen}
        onClose={() => {
          turnOnAfterSetup.current = false
          setKeyOpen(false)
        }}
        onSignIn={() => {
          setKeyOpen(false)
          signIn()
        }}
        signInLabel="Sign up with Readyset instead"
        onSuccess={() => {
          void invalidateTrialRelatedQueries(client)
          void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
          if (turnOnAfterSetup.current) allowAiAndTurnOn()
        }}
      />
      <AccountLoginDialog
        isOpen={signInOpen}
        signUp
        source="monitoring_gate"
        onClose={() => {
          turnOnAfterSetup.current = false
          setSignInOpen(false)
        }}
        onSuccess={() => {
          setSignInOpen(false)
          void invalidateTrialRelatedQueries(client)
          void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
          // The user asked to turn on once signed in, with the choices made here.
          if (turnOnAfterSetup.current) {
            trackEvent('monitoring_signin_gate_choice', {
              choice: 'signed_in_after',
            })
            allowAiAndTurnOn()
          }
        }}
      />
    </DialogShell>
  )
}
