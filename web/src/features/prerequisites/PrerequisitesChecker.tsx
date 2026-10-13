// Live monitoring prerequisites for one target. Each check is a row; failing
// rows open their provider-specific fix. The checker re-checks every few seconds
// while something fails, so fixes made in another window land on their own, and
// it finishes on a green tick once RDST can see the target's queries.
//
// Usage:
//   <PrerequisitesChecker target="orders" layout="page"
//     onContinue={finish} onSkip={finish} />       // onboarding
//   <PrerequisitesChecker target="orders" layout="panel" celebrate={false} />
import { cn } from '@rs/tailwind-base'
import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Skeleton } from '@rs/ui-new/skeleton'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { PrereqCheck } from './api'
import { FixPanel } from './FixPanel'
import {
  type CheckerPhase,
  firstOpenFix,
  headline,
  isRecommendation,
  orderedChecks,
  progress,
  statusWord,
} from './model'
import { type PrerequisitesState, usePrerequisites } from './usePrerequisites'

export interface PrerequisitesCheckerProps {
  target: string
  /** `page` is a raised card (onboarding); `panel` sits inside a drawer or dialog. */
  layout?: 'page' | 'panel'
  /** Finish on the ticked ready line. Defaults to true. */
  celebrate?: boolean
  /** Shown as the primary action once monitoring can start. */
  onContinue?: () => void
  continueLabel?: string
  /** Leave without finishing; shown while checks still fail. */
  onSkip?: () => void
  skipLabel?: string
  /** Fires when the phase changes (for callers that gate their own actions). */
  onPhaseChange?: (phase: CheckerPhase) => void
  /** Pre-fetched state, for callers that already hold the query. */
  state?: PrerequisitesState
  className?: string
}

// ---------------------------------------------------------------------------

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [active])
  return now
}

/** "Checking" while a request runs, else a countdown ring to the next check. */
function LiveIndicator({
  state,
  onRecheck,
}: {
  state: PrerequisitesState
  onRecheck: () => void
}) {
  const polling = state.interval !== false
  const now = useNow(polling)
  const reduced = useReducedMotion()
  const interval = typeof state.interval === 'number' ? state.interval : 0
  const remaining = polling
    ? Math.max(0, Math.ceil((state.updatedAt + interval - now) / 1000))
    : 0
  const fraction =
    polling && interval > 0
      ? Math.min(1, Math.max(0, (now - state.updatedAt) / interval))
      : 1
  const label = state.checking
    ? 'Checking'
    : polling
      ? `Next check in ${Math.max(1, remaining)} s`
      : 'Checked'
  return (
    <div className="flex shrink-0 items-center gap-1">
      <output
        aria-live="polite"
        aria-label={label}
        className="inline-flex h-7 shrink-0 items-center gap-2 whitespace-nowrap rounded-pill border border-border-layout-1 bg-surface-layout-1 px-2.5 text-label-small text-content-layout-2"
        data-testid="prereq-live"
        data-checking={state.checking ? 'true' : 'false'}
      >
        <span className="relative grid h-3.5 w-3.5 place-items-center">
          <svg
            viewBox="0 0 16 16"
            className="absolute inset-0"
            aria-hidden="true"
          >
            <circle
              cx="8"
              cy="8"
              r="6.5"
              fill="none"
              strokeWidth="1.5"
              className="stroke-border-layout-2"
            />
            {state.checking && !reduced ? (
              <m.circle
                cx="8"
                cy="8"
                r="6.5"
                fill="none"
                strokeWidth="1.5"
                strokeLinecap="round"
                className="stroke-content-layout-1"
                strokeDasharray="10 40.8"
                style={{ originX: '50%', originY: '50%' }}
                animate={{ rotate: 360 }}
                transition={{
                  repeat: Number.POSITIVE_INFINITY,
                  duration: 0.9,
                  ease: 'linear',
                }}
              />
            ) : (
              <circle
                cx="8"
                cy="8"
                r="6.5"
                fill="none"
                strokeWidth="1.5"
                strokeLinecap="round"
                className="stroke-content-layout-2 transition-[stroke-dashoffset] duration-slower ease-linear"
                strokeDasharray="40.84"
                strokeDashoffset={40.84 * (1 - fraction)}
                transform="rotate(-90 8 8)"
              />
            )}
          </svg>
        </span>
        <span className="tabular-nums">{label}</span>
      </output>
      <Button
        label="Check again"
        modifier="ghost"
        size="small"
        disabled={state.checking}
        onClick={onRecheck}
      />
    </div>
  )
}

const GLYPH: Record<
  string,
  { icon: 'tick' | 'alert' | 'info' | 'observe'; className: string }
> = {
  ok: {
    icon: 'tick',
    className: 'bg-surface-positive-soft text-content-positive-plain',
  },
  blocking: {
    icon: 'alert',
    className: 'bg-surface-warning-soft text-content-warning-plain',
  },
  recommended: {
    icon: 'info',
    className: 'bg-surface-layout-2 text-content-layout-2',
  },
  unknown: {
    icon: 'observe',
    className: 'bg-surface-layout-2 text-content-layout-3',
  },
}

function glyphFor(check: PrereqCheck) {
  if (check.status === 'ok') return GLYPH.ok
  if (check.status === 'unknown') return GLYPH.unknown
  if (check.status === 'waiting') return null
  return check.status === 'missing' && !isRecommendation(check)
    ? GLYPH.blocking
    : GLYPH.recommended
}

function StatusGlyph({
  check,
  checking,
}: {
  check: PrereqCheck
  checking: boolean
}) {
  const reduced = useReducedMotion()
  const glyph = glyphFor(check)
  return (
    <span className="relative grid h-7 w-7 shrink-0 place-items-center">
      <AnimatePresence initial={false} mode="popLayout">
        <m.span
          key={check.status}
          className={cn(
            'grid h-7 w-7 place-items-center rounded-pill',
            glyph
              ? glyph.className
              : 'border border-dashed border-border-layout-2 text-content-layout-3'
          )}
          initial={reduced ? { opacity: 0 } : { scale: 0.5, opacity: 0 }}
          animate={reduced ? { opacity: 1 } : { scale: 1, opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={
            reduced
              ? { duration: 0.15 }
              : { type: 'spring', stiffness: 420, damping: 22 }
          }
        >
          {glyph ? (
            <Icon name={glyph.icon} label="" size="small" />
          ) : (
            <span className="h-1.5 w-1.5 rounded-pill bg-content-layout-3" />
          )}
        </m.span>
      </AnimatePresence>
      {checking && check.status !== 'ok' && !reduced && (
        <m.span
          aria-hidden="true"
          className="absolute -inset-1 rounded-pill border-2 border-transparent border-t-border-layout-2"
          animate={{ rotate: 360 }}
          transition={{
            repeat: Number.POSITIVE_INFINITY,
            duration: 1,
            ease: 'linear',
          }}
        />
      )}
    </span>
  )
}

const TAG_TONE = {
  ok: 'positive',
  blocking: 'warning',
  neutral: 'neutral',
} as const

function StatusTag({ check }: { check: PrereqCheck }) {
  const tone =
    check.status === 'ok'
      ? TAG_TONE.ok
      : check.status === 'missing' && !isRecommendation(check)
        ? TAG_TONE.blocking
        : TAG_TONE.neutral
  return (
    <Tag
      size="small"
      variant={tone}
      modifier={tone === 'positive' ? 'ghost' : 'outline'}
      label={statusWord(check)}
      className="shrink-0"
    />
  )
}

function CheckRow({
  check,
  open,
  onToggle,
  checking,
  compact,
  database,
}: {
  check: PrereqCheck
  open: boolean
  onToggle: () => void
  checking: boolean
  compact: boolean
  database?: string
}) {
  const reduced = useReducedMotion()
  const fixable = Boolean(check.fix) && check.status !== 'ok'
  const flash = useRef(check.status)
  const [justPassed, setJustPassed] = useState(false)
  useEffect(() => {
    if (flash.current !== 'ok' && check.status === 'ok') {
      setJustPassed(true)
      const id = window.setTimeout(() => setJustPassed(false), 1400)
      flash.current = check.status
      return () => window.clearTimeout(id)
    }
    flash.current = check.status
  }, [check.status])

  return (
    <li
      data-check={check.id}
      data-status={check.status}
      className={cn(
        'relative transition-colors duration-slow ease-out',
        justPassed ? 'bg-surface-positive-soft' : 'bg-transparent'
      )}
    >
      <div
        className={cn(
          'flex items-start gap-3 px-3 tablet:px-4',
          compact ? 'py-2.5' : 'py-3.5'
        )}
      >
        <StatusGlyph check={check} checking={checking} />
        <div className="min-w-0 flex-1 space-y-0.5 pt-0.5">
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 tablet:flex-nowrap">
            <Text
              as="p"
              level="label-medium"
              className={cn(
                'min-w-0 text-content-layout-1 [overflow-wrap:anywhere] tablet:flex-1',
                check.status === 'waiting' && 'text-content-layout-3'
              )}
            >
              {check.status !== 'ok' && check.problem
                ? check.problem
                : check.label}
            </Text>
            {!compact && <StatusTag check={check} />}
          </div>
          {!compact && check.detail && (
            <Text
              as="p"
              level="body-small"
              className="text-content-layout-3 [overflow-wrap:anywhere]"
            >
              {check.detail}
            </Text>
          )}
          {!compact && fixable && (
            <Pressable
              onClick={onToggle}
              aria-expanded={open}
              className="-ml-1 mt-1 inline-flex items-center gap-1 rounded-control px-1 py-0.5 text-label-small text-content-layout-2 transition-colors duration-fast ease-base hover:bg-surface-layout-2 hover:text-content-layout-1"
            >
              {open
                ? 'Hide the fix'
                : check.problem
                  ? `Here's how to fix it${check.fix?.restart ? ' (needs a database restart)' : ''}`
                  : 'Show how to fix'}
              <Icon
                name="chevron-down"
                label=""
                size="small"
                className={cn(
                  'transition-transform duration-fast ease-base',
                  open && 'rotate-180'
                )}
              />
            </Pressable>
          )}
        </div>
      </div>
      <AnimatePresence initial={false}>
        {!compact && fixable && open && check.fix && (
          <m.div
            key="fix"
            initial={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduced ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-4 tablet:pl-14 tablet:pr-4">
              <div className="rounded-panel border border-border-layout-1 bg-surface-layout-1 p-3.5">
                <FixPanel fix={check.fix} database={database} />
              </div>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </li>
  )
}

function ProgressSegments({ checks }: { checks: PrereqCheck[] }) {
  const { required } = orderedChecks(checks)
  const { done, total } = progress(checks)
  return (
    <div className="flex items-center gap-3">
      <div
        role="progressbar"
        aria-label="Required checks passed"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="flex flex-1 gap-1"
      >
        {required.map((check) => (
          <span
            key={check.id}
            className={cn(
              'h-1.5 flex-1 rounded-pill transition-colors duration-slow ease-out',
              check.status === 'ok'
                ? 'bg-content-positive-plain'
                : check.status === 'missing'
                  ? 'bg-content-warning-plain'
                  : 'bg-border-layout-2'
            )}
          />
        ))}
      </div>
      <Text
        level="label-small"
        className="shrink-0 tabular-nums text-content-layout-2"
      >
        {done} of {total}
      </Text>
    </div>
  )
}

function CheckList({
  checks,
  checking,
  compact,
  database,
}: {
  checks: PrereqCheck[]
  checking: boolean
  compact: boolean
  database?: string
}) {
  const { required, optional } = orderedChecks(checks)
  const initial = firstOpenFix(checks)
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(initial ? [initial] : [])
  )
  // A fix opens by itself when its check starts failing after load.
  const seen = useRef(new Set(checks.map((check) => check.id)))
  useEffect(() => {
    const failing = firstOpenFix(checks)
    if (failing && !seen.current.has(`open:${failing}`)) {
      seen.current.add(`open:${failing}`)
      setOpen((current) => new Set(current).add(failing))
    }
  }, [checks])

  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const renderRows = (rows: PrereqCheck[], label: string) => (
    <ul
      aria-label={label}
      className="divide-y divide-border-layout-2 overflow-hidden rounded-panel border border-border-layout-2 bg-surface-raised"
    >
      {rows.map((check) => (
        <CheckRow
          key={check.id}
          check={check}
          open={open.has(check.id)}
          onToggle={() => toggle(check.id)}
          checking={checking}
          compact={compact && check.status === 'ok'}
          database={database}
        />
      ))}
    </ul>
  )

  return (
    <div className="space-y-4">
      {renderRows(required, 'Prerequisites')}
      {optional.length > 0 && (
        <div className="space-y-2">
          <Text as="h4" level="label-small" className="text-content-layout-3">
            Recommended
          </Text>
          {renderRows(optional, 'Recommended settings')}
        </div>
      )}
    </div>
  )
}

function ConnectingRows() {
  return (
    <ul
      aria-label="Prerequisites"
      aria-busy="true"
      className="divide-y divide-border-layout-2 rounded-panel border border-border-layout-2 bg-surface-raised"
    >
      {[0, 1, 2, 3].map((index) => (
        <li key={index} className="flex items-center gap-3 px-4 py-3.5">
          <Skeleton className="h-7 w-7 rounded-pill" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-1/2" />
            <Skeleton className="h-3 w-3/4" />
          </div>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------

export function PrerequisitesChecker({
  target,
  layout = 'panel',
  celebrate = true,
  onContinue,
  continueLabel = 'Continue',
  onSkip,
  skipLabel = 'Skip for now',
  onPhaseChange,
  state: provided,
  className,
}: PrerequisitesCheckerProps) {
  const own = usePrerequisites(target, { enabled: !provided })
  const state = provided ?? own
  const { result, phase } = state
  const polled = headline(phase, result, target)
  // While the connection is being edited nothing polls, so don't promise retries.
  const copy =
    phase === 'unreachable' && state.interval === false
      ? { ...polled, body: 'Fix the connection details, then check again.' }
      : polled
  const finished = phase === 'ready' || phase === 'attention'
  const showMark = celebrate && phase === 'ready'
  const reduced = useReducedMotion()

  const lastPhase = useRef<CheckerPhase | null>(null)
  useEffect(() => {
    if (lastPhase.current !== phase) {
      lastPhase.current = phase
      onPhaseChange?.(phase)
    }
  }, [phase, onPhaseChange])

  // The finish is the moment: bring the check mark to the middle of the view,
  // since the user is usually scrolled down to the fix they just made.
  const markRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) return
      window.requestAnimationFrame(() =>
        node.scrollIntoView?.({
          behavior: reduced ? 'auto' : 'smooth',
          block: 'center',
        })
      )
    },
    [reduced]
  )

  const page = layout === 'page'
  const database = result?.database ?? undefined

  return (
    <section
      aria-label={`Monitoring setup for ${target}`}
      data-testid="prereq-checker"
      data-phase={phase}
      className={cn(
        page &&
          'rounded-card border border-border-layout-1 bg-surface-raised p-4 shadow-elevation-1 tablet:p-7',
        className
      )}
    >
      <AnimatePresence mode="wait" initial={false}>
        {showMark ? (
          <m.div
            key="ready"
            ref={markRef}
            className="space-y-1.5 pb-4"
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
          >
            <div
              data-testid="prereq-ready-mark"
              className="flex items-center gap-2"
            >
              <Icon
                name="tick"
                label=""
                size="small"
                className="text-content-positive-soft"
              />
              <Text
                as="h3"
                level={page ? 'headline-3' : 'headline-5'}
                className="text-content-layout-1"
              >
                {copy.title}
              </Text>
            </div>
            <Text as="p" level="body-small" className="text-content-layout-3">
              {copy.body}
            </Text>
            {result?.provider_name && (
              <Text as="p" level="caption" className="text-content-layout-3">
                {result.provider_name}
              </Text>
            )}
            {onContinue && (
              <Button
                variant="primary"
                modifier="solid"
                label={continueLabel}
                onClick={onContinue}
                className="mt-3"
              />
            )}
          </m.div>
        ) : (
          <m.div
            key="working"
            className="space-y-4 pb-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  {phase === 'attention' && (
                    <Icon
                      name="tick"
                      label=""
                      size="small"
                      className="text-content-positive-soft"
                    />
                  )}
                  <Text
                    as="h3"
                    level={page ? 'headline-3' : 'headline-5'}
                    className="text-content-layout-1"
                  >
                    {copy.title}
                  </Text>
                  {result?.provider_name && (
                    <Text
                      as="span"
                      level="caption"
                      className="text-content-layout-3"
                    >
                      {result.provider_name}
                    </Text>
                  )}
                </div>
                <LiveIndicator state={state} onRecheck={state.recheck} />
              </div>
              <Text
                as="p"
                level="body-small"
                className="text-content-layout-3 [overflow-wrap:anywhere]"
              >
                {copy.body}
              </Text>
            </div>
            {result && result.checks.length > 0 && !finished && (
              <ProgressSegments checks={result.checks} />
            )}
          </m.div>
        )}
      </AnimatePresence>

      {phase === 'unreachable' && (
        <Alert
          variant="warning"
          modifier="outline"
          icon="alert"
          iconPosition="left"
          label={
            state.error?.message ??
            result?.connection_error ??
            `RDST couldn't connect to ${target}.`
          }
          className="mb-4"
        />
      )}

      {phase === 'connecting' && <ConnectingRows />}
      {result && result.checks.length > 0 && (
        <CheckList
          checks={result.checks}
          checking={state.checking}
          compact
          database={database}
        />
      )}

      {(onContinue || onSkip) && !showMark && (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          {onSkip && !finished ? (
            <Button label={skipLabel} modifier="ghost" onClick={onSkip} />
          ) : (
            <span />
          )}
          {onContinue && finished && (
            <Button
              variant="primary"
              modifier="solid"
              label={continueLabel}
              onClick={onContinue}
            />
          )}
        </div>
      )}
    </section>
  )
}
