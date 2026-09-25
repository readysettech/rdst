// Monitoring prerequisites after adding several targets at once (provider
// discovery, CSV import). One row per target with its verdict; the open row runs
// the live checker. When every target can be monitored, the large check mark
// takes the top.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Spinner } from '@rs/ui-new/spinner'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useCallback, useEffect, useState } from 'react'
import { EnableMonitoringDialog } from '@/features/database/settings/EnableMonitoringDialog'
import { useMonitorSettings } from '@/lib/monitor/hooks'
import { type CheckerPhase, progress } from './model'
import { PrerequisitesChecker } from './PrerequisitesChecker'
import { ReadyMark } from './ReadyMark'
import { type PrerequisitesState, usePrerequisites } from './usePrerequisites'

const VERDICT: Record<
  CheckerPhase,
  { label: string; tone: 'positive' | 'warning' | 'neutral' }
> = {
  connecting: { label: 'Checking', tone: 'neutral' },
  unreachable: { label: "Can't connect", tone: 'warning' },
  blocked: { label: 'Needs a fix', tone: 'warning' },
  attention: { label: 'Ready, SQL partly hidden', tone: 'positive' },
  ready: { label: 'Ready', tone: 'positive' },
}

// Once a database can be monitored, its row turns monitoring on, so leaving the list
// shows which databases are watched.
function MonitoringAction({
  target,
  onMonitored,
}: {
  target: string
  onMonitored: (target: string) => void
}) {
  const settings = useMonitorSettings(target)
  const [open, setOpen] = useState(false)
  const monitored = Boolean(settings.data?.enabled)
  useEffect(() => {
    if (monitored) onMonitored(target)
  }, [monitored, onMonitored, target])
  if (settings.data?.unavailable_reason)
    return (
      <Tag
        size="small"
        variant="neutral"
        label="Monitored through the writer"
        className="mr-4 shrink-0"
      />
    )
  if (monitored)
    return (
      <Tag
        size="small"
        variant="positive"
        label="Monitoring on"
        className="mr-4 shrink-0"
      />
    )
  return (
    <>
      <Button
        label="Turn on"
        modifier="outline"
        size="small"
        className="mr-4 shrink-0"
        onClick={() => setOpen(true)}
      />
      {open ? (
        <EnableMonitoringDialog
          target={target}
          openDatabase={false}
          open
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  )
}

function TargetRow({
  target,
  open,
  onToggle,
  onState,
  onMonitored,
}: {
  target: string
  open: boolean
  onToggle: () => void
  onState: (target: string, phase: CheckerPhase) => void
  onMonitored: (target: string) => void
}) {
  const reduced = useReducedMotion()
  const state: PrerequisitesState = usePrerequisites(target, { live: open })
  const verdict = VERDICT[state.phase]
  const count = state.result ? progress(state.result.checks) : null
  useEffect(() => {
    onState(target, state.phase)
  }, [onState, target, state.phase])
  return (
    <li data-target={target} data-phase={state.phase}>
      <div className="flex items-center hover:bg-surface-layout-2">
        <Pressable
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-left transition-colors duration-fast ease-base"
        >
          <span
            className={cn(
              'grid h-7 w-7 shrink-0 place-items-center rounded-pill',
              verdict.tone === 'positive'
                ? 'bg-surface-positive-soft text-content-positive-plain'
                : verdict.tone === 'warning'
                  ? 'bg-surface-warning-soft text-content-warning-plain'
                  : 'bg-surface-layout-2 text-content-layout-3'
            )}
          >
            {state.phase === 'connecting' ? (
              <Spinner />
            ) : (
              <Icon
                name={verdict.tone === 'positive' ? 'tick' : 'alert'}
                label=""
                size="small"
              />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <Text
              as="span"
              level="label-medium"
              className="block truncate text-content-layout-1"
            >
              {target}
            </Text>
            {state.result?.provider_name && (
              <Text
                as="span"
                level="caption"
                className="block truncate text-content-layout-3"
              >
                {state.result.provider_name}
                {count && count.total > 0
                  ? `, ${count.done} of ${count.total} checks passed`
                  : ''}
              </Text>
            )}
          </span>
          <Tag
            size="small"
            variant={verdict.tone}
            modifier={verdict.tone === 'positive' ? 'ghost' : 'outline'}
            label={verdict.label}
            className="shrink-0"
          />
          <Icon
            name="chevron-down"
            label=""
            size="small"
            className={cn(
              'shrink-0 text-content-layout-3 transition-transform duration-fast ease-base',
              open && 'rotate-180'
            )}
          />
        </Pressable>
        {state.phase === 'ready' || state.phase === 'attention' ? (
          <MonitoringAction target={target} onMonitored={onMonitored} />
        ) : null}
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <m.div
            key="checker"
            initial={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduced ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-border-layout-1 bg-surface-layout-1 p-4">
              <PrerequisitesChecker
                target={target}
                state={state}
                layout="panel"
                celebrate={false}
              />
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </li>
  )
}

export function TargetsPrerequisites({
  targets,
  onDone,
  onSkip,
}: {
  targets: string[]
  /** Called with the first database whose monitoring is on, if any. */
  onDone: (monitored?: string) => void
  onSkip?: () => void
}) {
  const [phases, setPhases] = useState<Record<string, CheckerPhase>>({})
  const [monitored, setMonitored] = useState<string[]>([])
  const addMonitored = useCallback((name: string) => {
    setMonitored((current) =>
      current.includes(name) ? current : [...current, name]
    )
  }, [])
  const [open, setOpen] = useState<string | null>(null)
  const report = useCallback((name: string, phase: CheckerPhase) => {
    setPhases((current) =>
      current[name] === phase ? current : { ...current, [name]: phase }
    )
  }, [])
  const firstProblem = targets.find(
    (target) => phases[target] === 'blocked' || phases[target] === 'unreachable'
  )
  const expanded = open ?? firstProblem ?? null
  const ready = targets.filter(
    (target) => phases[target] === 'ready' || phases[target] === 'attention'
  ).length
  const allReady = targets.length > 0 && ready === targets.length

  return (
    <div className="space-y-5" data-testid="targets-prerequisites">
      {allReady ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <ReadyMark />
          <Text as="h3" level="headline-3" className="text-content-layout-1">
            {targets.length === 1
              ? 'Ready to monitor'
              : `All ${targets.length} databases are ready to monitor`}
          </Text>
          <Text as="p" level="body-medium" className="text-content-layout-2">
            RDST can see the queries each one runs.
          </Text>
        </div>
      ) : (
        <div className="space-y-1">
          <Text as="h3" level="headline-5" className="text-content-layout-1">
            {ready} of {targets.length} ready to monitor
          </Text>
          <Text as="p" level="body-small" className="text-content-layout-3">
            Open a database to see what it needs. Fixes made in another window
            show up here on their own.
          </Text>
        </div>
      )}
      <ul className="divide-y divide-border-layout-2 overflow-hidden rounded-panel border border-border-layout-2 bg-surface-raised">
        {targets.map((target) => (
          <TargetRow
            key={target}
            target={target}
            open={expanded === target}
            onToggle={() =>
              setOpen((current) =>
                (current ?? expanded) === target ? '' : target
              )
            }
            onState={report}
            onMonitored={addMonitored}
          />
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {onSkip && !allReady ? (
          <Button label="Set up later" modifier="ghost" onClick={onSkip} />
        ) : (
          <span />
        )}
        {allReady && (
          <Button
            label="Done"
            icon="arrow-right"
            iconPosition="right"
            onClick={() =>
              onDone(targets.find((target) => monitored.includes(target)))
            }
          />
        )}
      </div>
    </div>
  )
}
