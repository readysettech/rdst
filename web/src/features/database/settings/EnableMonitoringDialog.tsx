// Turn on monitoring: a title, the prerequisites check, what it reads and runs, and one
// action that waits for the check to pass. Deep analysis measures real timings by default,
// with the EXPLAIN ANALYZE warning in view; the choices wait under Advanced. While a check
// fails, the dialog says plainly that monitoring can't start, with "Check again" and
// "Finish later". A database a coding agent just added lands here too, and the dialog
// follows the agent's progress until monitoring is on. The first turn-on is always the
// user's: when the agent asks for it, the dialog says it's their turn and waits.
// Monitoring's AI runs through a Readyset account, so a signed-out user is asked to sign
// in right here, and turning on without AI stays possible but discouraged.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useRouter } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
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
  // AI checks run through a Readyset account, even with an own key, so a signed-out user
  // signs in here. A full account or used-up AI usage holds deep analysis too.
  const limit = useMonitoringLimit(target, open).data
  const signedOut = useSignedOut()
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
  // Set by "Sign up or sign in": monitoring turns on once the sign-in finishes.
  const turnOnAfterSignIn = useRef(false)
  const initialized = useRef(false)
  const saved = settings.data
  useEffect(() => {
    if (!open) {
      initialized.current = false
      turnOnAfterSignIn.current = false
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
  const gated = signedOut && phase !== 'blocked' && phase !== 'unreachable'
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

  return (
    <DialogShell
      open={open}
      onClose={leave}
      title={`Monitor ${target}`}
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
        ) : gated ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              label="Turn on without AI"
              loading={update.isPending && !turnOnAfterSignIn.current}
              disabled={Boolean(unavailable) || !settings.data || !ready}
              onClick={() => {
                trackEvent('monitoring_signin_gate_choice', {
                  choice: 'without_ai',
                })
                turnOn()
              }}
              data-testid="monitoring-without-ai"
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Sign up or sign in"
              loading={update.isPending && turnOnAfterSignIn.current}
              disabled={Boolean(unavailable) || !settings.data || !ready}
              onClick={() => {
                trackEvent('monitoring_signin_gate_choice', {
                  choice: 'sign_in',
                })
                turnOnAfterSignIn.current = true
                setSignInOpen(true)
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
            disabled={Boolean(unavailable) || !settings.data || !ready}
            onClick={turnOn}
          />
        )
      }
    >
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
              {gated ? 'Sign up or sign in' : 'Turn on monitoring'}
            </Text>
            <Text
              as="span"
              level="caption"
              className="block text-content-layout-2"
            >
              Your agent asked RDST to monitor {target}. Read what monitoring
              reads and runs below, then turn it on yourself.
            </Text>
          </span>
        </output>
      )}
      {onlyDatabase && (
        <div className="mb-4">
          <GetStartedStrip step="monitor" />
        </div>
      )}
      {gated && (
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
            <Text as="p" level="label-medium" className="text-content-layout-1">
              Turning on monitoring needs AI, which is free with a Readyset
              account.
            </Text>
            <Text as="p" level="caption" className="mt-1 text-content-layout-2">
              AI finds what's slowing each query, works out the indexes worth
              adding and writes your nightly report. Sign up or sign in with
              Google or email, and monitoring turns on right after.
            </Text>
            <Text as="p" level="caption" className="mt-2 text-content-layout-3">
              Without AI, RDST only charts query stats and alerts on connection
              problems. Deep analysis, Actions and nightly reports don't run.
            </Text>
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
      <PrerequisitesChecker
        target={target}
        state={prerequisites}
        layout="panel"
        celebrate={false}
        recheckButton={phase !== 'blocked'}
      />
      <Text as="p" level="body-small" className="mt-4 text-content-layout-2">
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
            RDST will run EXPLAIN ANALYZE on problem queries. That runs each one
            for real: read-only SELECTs only, each with a time limit.
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
      <AccountLoginDialog
        isOpen={signInOpen}
        source="monitoring_gate"
        onClose={() => {
          turnOnAfterSignIn.current = false
          setSignInOpen(false)
        }}
        onSuccess={() => {
          setSignInOpen(false)
          void invalidateTrialRelatedQueries(client)
          void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
          // The user asked to turn on once signed in, with the choices made here.
          if (turnOnAfterSignIn.current) {
            trackEvent('monitoring_signin_gate_choice', {
              choice: 'signed_in_after',
            })
            turnOn()
          }
        }}
      />
    </DialogShell>
  )
}
