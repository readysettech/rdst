// Turn on monitoring: a title, the prerequisites check, what it reads and runs, and one
// action that waits for the check to pass. Deep analysis measures real timings by default,
// with the EXPLAIN ANALYZE warning in view; the choices wait under Advanced.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Disclosure } from '@rs/ui-new/disclosure'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useRouter } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { AccountLoginDialog } from '@/components/TrialRegistrationDialog'
import { selectGlobalTarget } from '@/features/database/targetSelection'
import {
  PrerequisitesChecker,
  usePrerequisites,
} from '@/features/prerequisites'
import {
  useMonitoringLimit,
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import { invalidateTrialRelatedQueries } from '@/lib/trialQueries'
import { DialogShell } from '../../../components/DialogShell'
import { SettingRow } from './SettingRow'

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
  // AI checks need a Readyset account: signed out, deep analysis waits for a sign-in. A full
  // account or used-up AI usage holds it too.
  const limit = useMonitoringLimit(target, open).data
  const signedOut = limit?.signed_in === false
  const deepWaits = signedOut
    ? 'once you sign in'
    : limit?.target_fits === false && limit.limit != null
      ? 'once an AI slot is free'
      : limit?.allowance?.includes('hosted') && !limit.own_key
        ? 'once your AI usage is raised'
        : null
  const update = useUpdateMonitorSettings(target)
  const client = useQueryClient()
  const [automatic, setAutomatic] = useState(true)
  const [measure, setMeasure] = useState(true)
  const [signInOpen, setSignInOpen] = useState(false)
  const initialized = useRef(false)
  const saved = settings.data
  useEffect(() => {
    if (!open) {
      initialized.current = false
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
  const note =
    phase === 'blocked'
      ? 'Run the setup steps above first. This check updates on its own once it passes.'
      : phase === 'unreachable'
        ? 'RDST needs to reach the database first.'
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
      onClose={onClose}
      title={`Monitor ${target}`}
      testId="enable-monitoring"
      note={note}
      action={
        signedOut ? (
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="primary"
              modifier="ghost"
              label="Turn on monitoring"
              loading={update.isPending}
              disabled={Boolean(unavailable) || !settings.data || !ready}
              onClick={turnOn}
            />
            <Button
              variant="primary"
              modifier="solid"
              label="Sign in to Readyset"
              onClick={() => setSignInOpen(true)}
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
      <PrerequisitesChecker
        target={target}
        state={prerequisites}
        layout="panel"
        celebrate={false}
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
      {signedOut && (
        <Text as="p" level="caption" className="mt-2 text-content-layout-2">
          You're signed out. Without a Readyset account, RDST only charts your
          database and alerts on connection problems. Deep analysis and Actions
          need you to sign in.
        </Text>
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
        onClose={() => setSignInOpen(false)}
        onSuccess={() => {
          // Back to this dialog, choices kept: the limit now says signed in.
          setSignInOpen(false)
          void invalidateTrialRelatedQueries(client)
          void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
        }}
      />
    </DialogShell>
  )
}
