import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { Progress } from '@rs/ui-new/progress'
import { Text } from '@rs/ui-new/text'
import { useDisclosure } from '@rs/ui-new/use-disclosure'
import { Link } from '@tanstack/react-router'
import { useId } from 'react'
import { trackEvent } from '../../lib/analytics'
import { useAiGate } from '../../lib/useAiGate'
import { dismissSetupGuide, useSetupGuideState } from './setupGuideStore'
import {
  completedSetupSteps,
  hasSetupSignal,
  isSetupComplete,
  SETUP_STEP_COUNT,
  type SetupStep,
  setupSteps,
} from './setupModel'
import {
  useSetupProgress,
  useSetupStepCompletionEvents,
} from './useSetupProgress'
import { useTopQueryHash } from './useTopQueryHash'

const ROW = 'flex items-center gap-2 rounded-md px-2 py-1'

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-primary-soft focus-visible:ring-offset-2 focus-visible:ring-offset-surface-layout-1'

/** Ordinal marker for a step that is still ahead; the tick replaces it once done. */
function StepMarker({ index, current }: { index: number; current: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-caption ${
        current
          ? 'border-border-primary-soft text-content-primary-soft'
          : 'border-border-layout-2 text-content-layout-3'
      }`}
    >
      {index + 1}
    </span>
  )
}

/**
 * The AI-key prerequisite for "Analyze a query", surfaced just-in-time under
 * the step that needs it rather than as a sixth row — a six-step list reads
 * harder than a five-step one. Mounted only while that step is the current
 * one, so the key probe never runs for a step nobody has reached.
 */
function AiKeyNote() {
  const gate = useAiGate()
  if (gate.status !== 'blocked') return null
  const label =
    gate.reason === 'exhausted'
      ? 'Your included AI is used up — sign in again or add a key'
      : gate.reason === 'invalid'
        ? 'This AI key was rejected — update it'
        : 'Needs an AI key — add one'
  return (
    <Link
      to="/configure"
      data-testid="setup-steps-ai-key-note"
      className={`ml-8 flex items-center gap-1.5 rounded-md px-2 py-0.5 text-content-warning-soft hover:bg-surface-warning-soft ${FOCUS_RING}`}
    >
      <Icon name="key" label="" aria-hidden="true" className="h-3.5 w-3.5" />
      <Text level="caption" className="text-content-warning-soft">
        {label}
      </Text>
    </Link>
  )
}

function StepRow({
  step,
  index,
  current,
}: {
  step: SetupStep
  index: number
  current: boolean
}) {
  if (step.done) {
    return (
      <div className={ROW}>
        <Icon
          name="tick"
          label="Done"
          className="h-4 w-4 shrink-0 text-content-positive-soft"
        />
        <Text level="caption" className="truncate text-content-layout-3">
          {step.label}
        </Text>
      </div>
    )
  }

  // Only the step the user is on carries an action: one live destination keeps
  // the block a status readout rather than a second navigation list. Its
  // reason rides on the row's title rather than a second line — the sidebar's
  // 320px would wrap that line in two and push the nav's last item off-screen.
  if (!current) {
    return (
      <div className={ROW}>
        <StepMarker index={index} current={false} />
        <Text level="caption" className="truncate text-content-layout-3">
          {step.label}
        </Text>
      </div>
    )
  }

  return (
    <Link
      to={step.to}
      search={step.search}
      aria-current="step"
      title={step.why}
      className={`${ROW} bg-surface-layout-2 hover:bg-surface-layout-soft motion-safe:transition-colors motion-safe:duration-fast ${FOCUS_RING}`}
    >
      <StepMarker index={index} current />
      <Text level="label-small" className="truncate text-content-layout-1">
        {step.label}
      </Text>
    </Link>
  )
}

/**
 * The setup checklist, docked in the sidebar footer above the utilities.
 *
 * It is chrome, not an overlay: the five steps live beside the navigation on
 * every route, so nothing the guide says can sit on top of the page it is
 * describing. All of its state is derived from `/api/setup-progress` except
 * the one persisted flag — the block is hidden once the user hides it, and it
 * retires on its own once every step is done. The sidebar's setup utility is
 * the way back.
 */
export function SetupSteps() {
  const { dismissed } = useSetupGuideState()
  // Collapsed by default: the header is a one-line progress readout, and the
  // steps unfold on request so the block never crowds the navigation.
  const [open, setOpen] = useDisclosure({})
  const panelId = useId()

  // Poll only for a block that is actually on screen with work left; a hidden
  // one still reads the signals on focus, which is what the utility that
  // brings it back needs.
  const { data } = useSetupProgress({ poll: !dismissed })
  useSetupStepCompletionEvents(data)

  const known = hasSetupSignal(data)
  const visible = known && !dismissed && !isSetupComplete(data)

  // Step 4 names a query rather than the library at large.
  const analyzeHash = useTopQueryHash(
    known ? data.target : undefined,
    visible && known && data.queries_found && !data.analyzed
  )

  if (!visible) return null

  const steps = setupSteps(data, { analyzeHash })
  const done = completedSetupSteps(data)
  const currentId = steps.find((step) => !step.done)?.id

  const handleDismiss = () => {
    dismissSetupGuide()
    trackEvent('setup_guide_dismissed')
  }

  return (
    <section
      aria-label="Setup guide"
      data-testid="setup-steps"
      className="mt-4"
    >
      <div className="flex items-start gap-1">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen(!open)}
          data-testid="setup-steps-toggle"
          className={`flex min-w-0 flex-1 flex-col gap-1.5 rounded-md px-2 py-1.5 text-left hover:bg-surface-layout-2 motion-safe:transition-colors motion-safe:duration-fast ${FOCUS_RING}`}
        >
          <span className="flex w-full items-center gap-2">
            <Text
              as="span"
              level="label-small"
              className="text-content-layout-2"
            >
              Setup
            </Text>
            <Text as="span" level="caption" className="text-content-layout-3">
              {done} of {SETUP_STEP_COUNT}
            </Text>
            <Icon
              name="chevron-down"
              label=""
              aria-hidden="true"
              className={`ml-auto h-4 w-4 shrink-0 text-content-layout-3 motion-safe:transition-transform motion-safe:duration-fast ${
                open ? 'rotate-0' : '-rotate-90'
              }`}
            />
          </span>
          <Progress
            value={done}
            max={SETUP_STEP_COUNT}
            label="Setup progress"
            className="h-1"
          />
        </button>
        <IconButton
          icon="close"
          label="Hide setup guide"
          variant="primary"
          modifier="ghost"
          size="small"
          tooltip={false}
          onClick={handleDismiss}
          data-testid="setup-steps-dismiss"
          className="mt-0.5 shrink-0"
        />
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <m.ol
            id={panelId}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="flex flex-col overflow-hidden"
          >
            {steps.map((step, index) => (
              <li
                key={step.id}
                data-testid={`setup-step-${step.id}`}
                data-done={String(step.done)}
                data-current={String(step.id === currentId)}
              >
                <StepRow
                  step={step}
                  index={index}
                  current={step.id === currentId}
                />
                {step.id === 'analyze-query' && step.id === currentId ? (
                  <AiKeyNote />
                ) : null}
              </li>
            ))}
          </m.ol>
        )}
      </AnimatePresence>
    </section>
  )
}
