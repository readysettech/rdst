import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Pressable } from '@rs/ui-new/pressable'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { Link, useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { trackEvent } from '../../lib/analytics'
import { useAiGate } from '../../lib/useAiGate'
import {
  dismissSetupGuide,
  markSetupGuideAutoExpanded,
  useSetupGuideState,
} from './setupGuideStore'
import {
  completedSetupSteps,
  hasSetupSignal,
  isSetupComplete,
  isSetupGuideSuppressed,
  SETUP_STEP_COUNT,
  type SetupStep,
  setupSteps,
} from './setupModel'
import {
  useSetupProgress,
  useSetupStepCompletionEvents,
} from './useSetupProgress'
import { useTopQueryHash } from './useTopQueryHash'

/**
 * Thin completion bar. Deliberately `aria-hidden`: the "N of 5" count next to
 * it carries the same information in words, so the bar is decoration and a
 * second progressbar role would only repeat it. [USE-004, VIS-119]
 */
function ProgressBar({ done, className }: { done: number; className: string }) {
  return (
    <span
      aria-hidden="true"
      className={`block overflow-hidden rounded-full bg-border-layout-1 ${className}`}
    >
      <span
        className="block h-full rounded-full bg-content-rising-plain transition-all duration-slower ease-base"
        style={{ width: `${(done / SETUP_STEP_COUNT) * 100}%` }}
      />
    </span>
  )
}

/**
 * The AI-key prerequisite for "Analyze a query", surfaced just-in-time under
 * the step that needs it rather than as a sixth row — a six-step list reads
 * harder than a five-step one. Mounted only inside the open panel, so the key
 * probe never runs for a guide nobody opened.
 */
function AiKeyNote({ onNavigate }: { onNavigate: () => void }) {
  const gate = useAiGate()
  if (gate.status !== 'blocked') return null
  const label =
    gate.reason === 'exhausted'
      ? 'Free AI credits are used up — add a key'
      : gate.reason === 'invalid'
        ? 'This AI key was rejected — update it'
        : 'Needs an AI key — add one'
  return (
    <Link
      to="/configure"
      onClick={onNavigate}
      data-testid="setup-guide-ai-key-note"
      className="mt-1 ml-8 inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-content-warning-soft hover:bg-surface-warning-soft"
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
  onNavigate,
}: {
  step: SetupStep
  index: number
  onNavigate: () => void
}) {
  if (step.done) {
    // Completed steps collapse to label + check and recede. [VIS-011]
    return (
      <div className="flex items-center gap-2 px-2 py-1.5">
        <Icon
          name="tick"
          label="Done"
          className="h-4 w-4 shrink-0 text-content-positive-soft"
        />
        <Text level="body-small" className="text-content-layout-3">
          {step.label}
        </Text>
      </div>
    )
  }

  return (
    <Link
      to={step.to}
      search={step.search}
      onClick={onNavigate}
      className="flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-surface-layout-2"
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border-layout-2 text-caption text-content-layout-3"
      >
        {index + 1}
      </span>
      <VStack className="min-w-0 items-start gap-0">
        <Text level="body-small" className="text-content-layout-1">
          {step.label}
        </Text>
        <Text level="caption" className="text-content-layout-3">
          {step.why}
        </Text>
      </VStack>
    </Link>
  )
}

/**
 * The setup guide (RDST UX plan, workstream D): a floating progress mirror for
 * the one thing self-evident screens cannot carry — a multi-step, cross-screen
 * setup with real prerequisites.
 *
 * State machine, all of it derived except two persisted flags:
 *  - hidden while the signals are unknown, on `/demo` and `/onboarding`, once
 *    all five steps are complete (completing is the exit), and once dismissed;
 *  - expands automatically exactly once per install, on Home and without
 *    stealing focus;
 *  - dismissal survives restarts and is recoverable only from the sidebar.
 *
 * It is not a modal, not a tour, not a gate, and it carries no badge.
 */
export function SetupGuide() {
  const router = useRouterState()
  const suppressed = isSetupGuideSuppressed(router.location.pathname)
  const onHome = router.location.pathname === '/'
  const { dismissed, autoExpanded, openRequest } = useSetupGuideState()
  const [open, setOpen] = useState(false)
  const autoOpened = useRef(false)

  // Poll only for a guide that is actually on screen with work left; a
  // suppressed or dismissed guide still reads the signals on focus, which is
  // what the sidebar's recovery entry needs.
  const { data } = useSetupProgress({ poll: !suppressed && !dismissed })
  useSetupStepCompletionEvents(data)

  const known = hasSetupSignal(data)
  const visible = known && !suppressed && !dismissed && !isSetupComplete(data)

  // Step 4 names a query rather than the library at large. Asked for only
  // while an open panel still has that step to do.
  const analyzeHash = useTopQueryHash(
    known ? data.target : undefined,
    open && visible && known && data.queries_found && !data.analyzed
  )

  // The one automatic expansion, on Home. Onboarding lands there, so the guide
  // still introduces itself once, and an open panel is never in the way of a
  // page the user navigated to in order to do something. Persisted before the
  // panel opens, so a reload mid-expansion never earns a second one.
  useEffect(() => {
    if (!visible || autoExpanded || !onHome) return
    markSetupGuideAutoExpanded()
    autoOpened.current = true
    setOpen(true)
  }, [visible, autoExpanded, onHome])

  // Reopened from the sidebar footer's setup guide entry.
  const lastRequest = useRef(openRequest)
  useEffect(() => {
    if (openRequest === lastRequest.current) return
    lastRequest.current = openRequest
    autoOpened.current = false
    setOpen(true)
    trackEvent('setup_guide_opened')
  }, [openRequest])

  if (!visible) return null

  const steps = setupSteps(data, { analyzeHash })
  const done = completedSetupSteps(data)

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) return
    autoOpened.current = false
    trackEvent('setup_guide_opened')
  }

  const handleDismiss = () => {
    setOpen(false)
    dismissSetupGuide()
    trackEvent('setup_guide_dismissed')
  }

  return (
    // Fixed above content and below modals/drawers, so appearing costs no
    // layout shift anywhere on the page.
    <div className="fixed bottom-4 right-4 z-40">
      <Popover open={open} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <Pressable
            type="button"
            data-testid="setup-guide-pill"
            aria-label={`Setup guide, ${done} of ${SETUP_STEP_COUNT} steps done`}
            className="flex cursor-pointer items-center gap-2 rounded-full border border-border-layout-1 bg-surface-layout-1 px-3 py-2 shadow-elevation-2 hover:bg-surface-layout-2"
          >
            <Icon
              name="road-wayside"
              label=""
              aria-hidden="true"
              className="h-4 w-4 shrink-0 text-content-layout-3"
            />
            <Text level="label-small" className="text-content-layout-1">
              Setup · {done} of {SETUP_STEP_COUNT}
            </Text>
            <ProgressBar done={done} className="h-1 w-10" />
          </Pressable>
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="end"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Setup guide"
          data-testid="setup-guide-panel"
          // An automatic expansion must never steal focus; a deliberate open
          // still moves focus into the panel so it is keyboard-operable.
          onOpenAutoFocus={(event) => {
            if (autoOpened.current) event.preventDefault()
          }}
          className="w-96 max-w-[calc(100vw-1.5rem)] flex-col p-0"
        >
          <VStack className="items-stretch gap-3 p-4">
            <HStack className="items-start justify-between gap-2">
              <VStack className="items-start gap-0.5">
                <Text level="label-medium" className="text-content-layout-1">
                  Finish setting up Readyset
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  {done} of {SETUP_STEP_COUNT} done
                </Text>
              </VStack>
              <IconButton
                icon="close"
                label="Dismiss setup guide"
                variant="primary"
                modifier="ghost"
                size="small"
                tooltip={false}
                onClick={handleDismiss}
                data-testid="setup-guide-dismiss"
              />
            </HStack>

            <ProgressBar done={done} className="h-1 w-full" />

            <ul className="flex flex-col gap-0.5">
              {steps.map((step, index) => (
                <li
                  key={step.id}
                  data-testid={`setup-step-${step.id}`}
                  data-done={String(step.done)}
                >
                  <StepRow
                    step={step}
                    index={index}
                    onNavigate={() => setOpen(false)}
                  />
                  {step.id === 'analyze-query' && !step.done ? (
                    <AiKeyNote onNavigate={() => setOpen(false)} />
                  ) : null}
                </li>
              ))}
            </ul>
          </VStack>
        </PopoverContent>
      </Popover>
    </div>
  )
}
