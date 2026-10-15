import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import type { ReactNode } from 'react'

export type GetStartedStep = 'sign-in' | 'add-database'

const STEPS: {
  key: GetStartedStep | 'monitor'
  title: string
  short: string
  caption: string
}[] = [
  {
    key: 'sign-in',
    title: 'Sign in to Readyset',
    short: 'Sign in',
    caption: 'AI analysis is included, with no API key to manage.',
  },
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
      'RDST checks every query each minute. Your first fixes show up in about 10 minutes.',
  },
]

/**
 * The first-run screen: what RDST does, then three numbered steps with one big
 * button on the current one. Sign-in and the empty Home both render it, at their
 * own step, so a new install always shows the next thing to do.
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
  return (
    <div className="max-w-2xl" data-testid="get-started">
      <Text as="h2" level="headline-2" className="text-content-layout-1">
        Find out what's slowing your database.
      </Text>
      <Text as="p" level="body-medium" className="mt-3 text-content-layout-3">
        RDST watches your database and hands you the fix for every slow query,
        with the SQL to run.
      </Text>
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

/** The three steps as one compact row, for the sign-in dialog. */
export function GetStartedStrip({ step }: { step: GetStartedStep }) {
  const current = STEPS.findIndex((item) => item.key === step)
  return (
    <ol className="grid grid-cols-3 gap-2" data-testid="get-started-strip">
      {STEPS.map((item, index) => {
        const done = index < current
        const active = index === current
        return (
          <li
            key={item.key}
            aria-current={active ? 'step' : undefined}
            className={`flex items-center gap-2 rounded-control px-2.5 py-2 ${
              active ? 'bg-surface-layout-1' : ''
            }`}
          >
            <span
              className={`grid size-5 shrink-0 place-items-center rounded-full text-caption tabular-nums ${
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
            <Text
              level="caption"
              className={
                active ? 'text-content-layout-1' : 'text-content-layout-3'
              }
            >
              {item.short}
            </Text>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * True the first time a first-run pop-up asks to open in this app session, so it
 * opens on its own once and stays closed after the user closes it.
 */
export function claimFirstRunPopup(key: 'sign-in' | 'add-database'): boolean {
  // Browser automation drives these screens step by step, so the pop-ups wait for
  // a click there unless a test asks for them.
  const optIn = (window as { __RDST_FIRST_RUN_POPUPS__?: boolean })
    .__RDST_FIRST_RUN_POPUPS__
  if (navigator.webdriver && !optIn) return false
  const storageKey = `rdst_get_started_${key}`
  try {
    if (sessionStorage.getItem(storageKey)) return false
    sessionStorage.setItem(storageKey, '1')
  } catch {
    /* without storage it opens on each visit to the page */
  }
  return true
}
