import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Progress } from '@rs/ui-new/progress'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useEffect } from 'react'
import { tourPending } from '../features/onboarding/onboardingState'
import { TourEntry } from '../features/onboarding/TourEntry'
import { type OnboardingStep, trackEventOnce } from '../lib/analytics'
import { scopedKey } from '../lib/installationScope'

export type GetStartedStep = OnboardingStep

const STEPS: {
  key: GetStartedStep
  title: string
  short: string
  caption: string
}[] = [
  {
    key: 'add-database',
    title: 'Connect your database',
    short: 'Connect database',
    caption: 'PostgreSQL or MySQL. RDST only reads from it.',
  },
  {
    key: 'monitor',
    title: 'Turn on monitoring',
    short: 'Turn on monitoring',
    caption:
      'Sign in there for free AI. RDST checks every query each minute, and your first fixes show up in about 10 minutes.',
  },
]

/** Reports each step's first view in an app session. */
function useStepViewed(step: GetStartedStep) {
  useEffect(() => {
    trackEventOnce(`onboarding-view-${step}`, 'onboarding_step_viewed', {
      step,
    })
  }, [step])
}

/**
 * The first-run screen: what RDST does, then numbered steps with one big button on
 * the current one. The empty Home renders it at connecting a database, so a new
 * install always shows the next thing to do. Signing in happens when monitoring
 * is turned on, since that's where AI starts.
 */
export function GetStarted({
  step,
  action,
  doneCaption,
  footer,
}: {
  step: GetStartedStep
  /** The current step's button. */
  action: ReactNode
  /** Replaces the caption of finished steps, such as who is signed in. */
  doneCaption?: string
  /** Quiet alternatives under the steps. */
  footer?: ReactNode
}) {
  const current = STEPS.findIndex((item) => item.key === step)
  useStepViewed(step)
  return (
    <div className="max-w-2xl" data-testid="get-started">
      <Text as="h2" level="headline-2" className="text-content-layout-1">
        Find out what's slowing your database.
      </Text>
      <Text as="p" level="body-medium" className="mt-3 text-content-layout-3">
        RDST watches your database and hands you the fix for every slow query,
        with the SQL to run.
      </Text>
      <TourEntry className="mt-2 -ml-3" />
      <ol className="mt-9 flex flex-col gap-3">
        {STEPS.map((item, index) => {
          const done = index < current
          const active = index === current
          return (
            <li
              key={item.key}
              aria-current={active ? 'step' : undefined}
              className={`flex items-center gap-4 rounded-card border px-5 py-4 ${
                active
                  ? 'border-border-layout-2 bg-surface-layout-1'
                  : 'border-transparent'
              }`}
            >
              <span
                className={`grid size-8 shrink-0 place-items-center rounded-full text-body-small tabular-nums ${
                  done
                    ? 'bg-surface-positive-soft text-content-positive-soft'
                    : active
                      ? 'bg-surface-primary-solid text-content-primary-solid'
                      : 'bg-surface-layout-1 text-content-layout-3'
                }`}
              >
                {done ? (
                  <Icon name="tick" label="Done" size="small" />
                ) : (
                  index + 1
                )}
              </span>
              <div className="min-w-0 flex-1">
                <Text
                  level="body-medium"
                  className={
                    active || done
                      ? 'text-content-layout-1'
                      : 'text-content-layout-3'
                  }
                >
                  {item.title}
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  {done && doneCaption ? doneCaption : item.caption}
                </Text>
              </div>
              {active && action}
            </li>
          )
        })}
      </ol>
      {footer && <div className="mt-5 pl-5">{footer}</div>}
    </div>
  )
}

export function GetStartedButton({
  label,
  onClick,
}: {
  label: string
  onClick: () => void
}) {
  return (
    <Button
      variant="primary"
      modifier="solid"
      size="large"
      label={label}
      onClick={onClick}
      data-testid="get-started-action"
    />
  )
}

/**
 * Where a first-run dialog sits among the steps: a progress bar over the step's
 * name and the one after it.
 */
export function GetStartedStrip({ step }: { step: GetStartedStep }) {
  const current = STEPS.findIndex((item) => item.key === step)
  useStepViewed(step)
  const next = STEPS[current + 1]
  return (
    <div className="flex flex-col gap-2" data-testid="get-started-strip">
      <Progress
        tone="quiet"
        // The default track is the dialog surface's own color; the field inset keeps
        // the steps still to come visible.
        className="bg-surface-layout-1"
        value={current + 1}
        max={STEPS.length}
        label="Getting started"
      />
      <div className="flex items-baseline justify-between gap-3">
        <Text level="caption" className="text-content-layout-3">
          Step {current + 1} of {STEPS.length}
          {' · '}
          <span className="text-content-layout-1">{STEPS[current]?.short}</span>
        </Text>
        {next && (
          <Text level="caption" className="text-content-layout-3">
            Next: {next.short}
          </Text>
        )}
      </div>
    </div>
  )
}

/**
 * True the first time a first-run pop-up asks to open in this app session, so it
 * opens on its own once and stays closed after the user closes it.
 */
export function claimFirstRunPopup(key: 'add-database'): boolean {
  // Browser automation drives these screens step by step, so the pop-ups wait for
  // a click there unless a test asks for them.
  const optIn = (window as { __RDST_FIRST_RUN_POPUPS__?: boolean })
    .__RDST_FIRST_RUN_POPUPS__
  if (navigator.webdriver && !optIn) return false
  // A new install sees the tour first; finishing it opens this dialog.
  if (tourPending()) return false
  try {
    if (sessionStorage.getItem(firstRunPopupKey(key))) return false
  } catch {
    /* without storage it opens on each visit to the page */
  }
  markFirstRunPopupShown(key)
  return true
}

const firstRunPopupKey = (key: 'add-database') =>
  scopedKey(`rdst_get_started_${key}`)

/** The pop-up opened another way, such as from the tour, so it doesn't open again. */
export function markFirstRunPopupShown(key: 'add-database'): void {
  try {
    sessionStorage.setItem(firstRunPopupKey(key), '1')
  } catch {
    /* without storage it opens on each visit to the page */
  }
}
