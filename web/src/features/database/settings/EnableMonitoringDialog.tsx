// Turn on monitoring with prerequisites, sign-in guidance and deep analysis options.
import { Alert } from '@rs/ui-new/alert'
import { BaseInputRadioGroup } from '@rs/ui-new/base-input-radio-group'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useRouter } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { TrialRegistrationDialog } from '@/components/TrialRegistrationDialog'
import { selectGlobalTarget } from '@/features/database/targetSelection'
import { ScrollDialog } from '@/features/monitor-kit/ScrollDialog'
import {
  PrerequisitesChecker,
  usePrerequisites,
} from '@/features/prerequisites'
import { isDesktopMac } from '@/lib/desktop'
import {
  useMonitoringLimit,
  useMonitorSettings,
  useUpdateMonitorSettings,
} from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'
import { invalidateTrialRelatedQueries } from '@/lib/trialQueries'
import { ConnectionRepair } from './ConnectionRepair'
import {
  EXECUTION_OPTIONS,
  type ExecutionMode,
  enableAction,
} from './prerequisites'

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2.5">
      <Text as="h3" level="label-medium" className="text-content-layout-1">
        {title}
      </Text>
      {children}
    </section>
  )
}

export const SIGNED_OUT_LINE =
  "You're signed out, so monitoring runs locally with charts and connection alerts only. Sign in to turn on AI monitoring."
export const SIGNED_IN_LINE =
  "You're signed in, so turning on monitoring includes AI checks through Readyset."
export const OWN_KEY_LINE =
  "Your own Anthropic key covers deep analysis and reports. It doesn't replace signing in."

/**
 * The connection editor state for a monitoring dialog. It opens by itself the first
 * time the target turns out to be unreachable, and the checks stop polling while the
 * user edits.
 */
export function useConnectionRepair(target: string, open: boolean) {
  const [editing, setEditing] = useState(false)
  const [offered, setOffered] = useState(false)
  const prerequisites = usePrerequisites(target, {
    enabled: open,
    live: !editing,
  })
  const unreachable = prerequisites.phase === 'unreachable'
  useEffect(() => {
    if (!open) {
      setEditing(false)
      setOffered(false)
    } else if (unreachable && !offered) {
      setOffered(true)
      setEditing(true)
    }
  }, [open, unreachable, offered])
  return { prerequisites, editing, setEditing }
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
  const [deepAnalysis, setDeepAnalysis] = useState(true)
  const [mode, setMode] = useState<ExecutionMode>('execute')
  const update = useUpdateMonitorSettings(target)
  const repair = useConnectionRepair(target, open)
  const { prerequisites } = repair
  const settings = useMonitorSettings(target)
  const initialized = useRef<string | null>(null)
  useEffect(() => {
    if (!open) {
      initialized.current = null
    } else if (settings.data && initialized.current !== target) {
      initialized.current = target
      const saved = settings.data
      const configured =
        saved.configured ?? (saved.enabled_at != null || saved.enabled)
      setDeepAnalysis(configured ? saved.deep_analysis : true)
      setMode(
        configured
          ? (saved.analysis_mode ?? (saved.execute ? 'execute' : 'plans_only'))
          : 'execute'
      )
    }
  }, [open, target, settings.data])
  const unavailable = settings.data?.unavailable_reason
  const checked = enableAction(prerequisites.phase)
  const action = unavailable
    ? { ...checked, enabled: false, note: unavailable }
    : checked
  const limit = useMonitoringLimit(target, open)
  const client = useQueryClient()
  const [signInOpen, setSignInOpen] = useState(false)
  // Signed out, AI monitoring can't start: signing in is the main way forward.
  const signedOut = limit.data?.signed_in === false
  const buttonLabel =
    signedOut && action.label === 'Turn on monitoring'
      ? 'Turn on local monitoring'
      : action.label
  const turnOn = () =>
    update.mutate(
      {
        enabled: true,
        deep_analysis: deepAnalysis,
        execute: mode === 'execute',
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

  // In the footer so a refusal shows next to the button however far the dialog is
  // scrolled.
  const failure = update.isError ? (
    <Alert
      variant="negative"
      modifier="outline"
      icon="alert"
      iconPosition="left"
      label={
        update.error instanceof Error
          ? update.error.message
          : "Couldn't turn on monitoring"
      }
    />
  ) : null

  const footer = (
    <div className="flex flex-col gap-3">
      {failure}
      <div
        className={
          signedOut
            ? 'flex flex-col gap-3'
            : 'flex flex-col gap-3 tablet:flex-row tablet:items-center tablet:justify-between'
        }
      >
        <Text as="p" level="caption" className="text-content-layout-3">
          {action.note ??
            `Monitoring keeps running in the ${isDesktopMac() ? 'menu bar' : 'system tray'} after you close this window.`}
        </Text>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Button label="Not now" modifier="ghost" onClick={onClose} />
          <Button
            label={buttonLabel}
            modifier={
              prerequisites.phase === 'blocked' || signedOut
                ? 'outline'
                : 'solid'
            }
            loading={update.isPending}
            disabled={!action.enabled || !settings.data}
            onClick={turnOn}
          />
          {signedOut ? (
            <Button
              label="Sign in to turn on AI monitoring"
              variant="rising"
              onClick={() => setSignInOpen(true)}
            />
          ) : null}
        </div>
      </div>
    </div>
  )

  return (
    <ScrollDialog
      open={open}
      onClose={onClose}
      title={`Turn on monitoring for ${target}`}
      description="RDST reads query statistics every minute and helps you spot queries that need attention."
      footer={footer}
      testId="enable-monitoring"
    >
      <div className="space-y-6">
        <PrerequisitesChecker
          target={target}
          state={prerequisites}
          layout="panel"
          celebrate={false}
        />
        {prerequisites.phase === 'unreachable' || repair.editing ? (
          <ConnectionRepair
            target={target}
            editing={repair.editing}
            onEditingChange={repair.setEditing}
            onSaved={prerequisites.recheck}
          />
        ) : null}

        <Section title="AI monitoring">
          {limit.data?.signed_in ? (
            <Text as="p" level="caption" className="text-content-layout-2">
              {SIGNED_IN_LINE}
            </Text>
          ) : null}
          {signedOut ? (
            <output
              data-testid="ai-monitoring-sign-in"
              className="block space-y-1 rounded-panel border border-border-rising-soft bg-surface-rising-soft p-3"
            >
              <Text
                as="p"
                level="label-medium"
                className="text-content-rising-soft"
              >
                Sign in to turn on AI monitoring
              </Text>
              <Text as="p" level="caption" className="text-content-layout-2">
                {SIGNED_OUT_LINE}
              </Text>
              {limit.data?.own_key ? (
                <Text as="p" level="caption" className="text-content-layout-2">
                  {OWN_KEY_LINE}
                </Text>
              ) : null}
            </output>
          ) : null}
        </Section>

        <Section title="Deep analysis">
          <div className="flex items-start gap-3 rounded-panel border border-border-layout-1 p-3">
            <BaseInputSwitch
              name="enable-deep-analysis"
              checked={deepAnalysis}
              onCheckedChange={setDeepAnalysis}
              aria-label="Include deep analysis"
            />
            <div>
              <Text
                as="p"
                level="label-medium"
                className="text-content-layout-1"
              >
                Include deep analysis
              </Text>
              <Text as="p" level="caption" className="text-content-layout-3">
                The busiest queries get their first deep analysis within a few
                minutes, one at a time. New top queries get theirs as they show
                up.
              </Text>
            </div>
          </div>
        </Section>

        <Section title="When RDST analyzes a problem query">
          <BaseInputRadioGroup
            aria-label="When RDST analyzes a problem query"
            value={mode}
            disabled={!deepAnalysis}
            onValueChange={(value) => setMode(value as ExecutionMode)}
            options={EXECUTION_OPTIONS.map((option) => ({
              ...option,
              badge:
                option.value === 'execute' ? (
                  <Tag
                    size="small"
                    variant="neutral"
                    modifier="outline"
                    label="Recommended"
                  />
                ) : undefined,
            }))}
          />
          {deepAnalysis && mode === 'execute' ? (
            <output
              data-testid="explain-analyze-warning"
              className="flex items-start gap-2 rounded-panel border border-border-warning-soft bg-surface-warning-soft px-3 py-2"
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
          ) : null}
          <Text as="p" level="caption" className="text-content-layout-3">
            {deepAnalysis
              ? "This applies to automatic analyses and the ones you start. You can change it later in this database's settings."
              : 'Turn on deep analysis to choose how RDST reads a problem query.'}
          </Text>
        </Section>
      </div>
      <TrialRegistrationDialog
        isOpen={signInOpen}
        onClose={() => setSignInOpen(false)}
        onSuccess={() => {
          // Back to this dialog, choices kept: the limit now says signed in.
          setSignInOpen(false)
          void invalidateTrialRelatedQueries(client)
          void client.invalidateQueries({ queryKey: monitorKeys.limitAll })
        }}
      />
    </ScrollDialog>
  )
}
