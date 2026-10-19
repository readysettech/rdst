// The sign-in prompts for hosted AI without an account, one per `signin_prompt` state:
// a dismissible line after the first week, a card on Home and the Inbox once most of the
// free allowance is used, and the blocking step once it has ended.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { SignInTrustNote } from '../features/onboarding/TrustNote'
import {
  type SigninPrompt,
  signinPromptOf,
  useAccountStatus,
} from '../lib/accountStatus'
import { type SigninPromptState, trackEventOnce } from '../lib/analytics'
import type { AccountStatusResponse } from '../lib/api'
import { AccountLoginDialog } from './TrialRegistrationDialog'

export const SOFT_PROMPT =
  'Sign in to keep your monitoring history and get alerts by email.'
export const BUDGET_PROMPT = 'Sign in to keep AI monitoring running'

const SOFT_HIDDEN_KEY = 'rdst_signin_soft_hidden_at'
const SOFT_HIDDEN_FOR_MS = 3 * 24 * 60 * 60 * 1000

function softHiddenUntil(): number {
  try {
    const at = Number(localStorage.getItem(SOFT_HIDDEN_KEY))
    return Number.isFinite(at) && at > 0 ? at + SOFT_HIDDEN_FOR_MS : 0
  } catch {
    return 0
  }
}

function hideSoft() {
  try {
    localStorage.setItem(SOFT_HIDDEN_KEY, String(Date.now()))
  } catch {
    /* without storage the line comes back next launch */
  }
}

function usePromptShown(state: SigninPromptState, shown: boolean) {
  useEffect(() => {
    if (shown)
      trackEventOnce(`signin-prompt-${state}`, 'signin_prompt_shown', { state })
  }, [state, shown])
}

/** The prompt state on screen, or null when no prompt is due. */
function usePrompt(): {
  prompt: SigninPrompt | null
  account: AccountStatusResponse | undefined
} {
  const { data } = useAccountStatus()
  return { prompt: signinPromptOf(data), account: data }
}

/** "You've used 84% of the free AI on this computer." */
function usedLine(account: AccountStatusResponse | undefined): string {
  const percent = account?.percent_used
  return percent != null
    ? `You've used ${Math.min(100, Math.round(percent))}% of the free AI on this computer.`
    : "You've used most of the free AI on this computer."
}

/**
 * After a week without an account: one line under the page bar, hidden for three days
 * when dismissed.
 */
export function SigninSoftStrip() {
  const { prompt } = usePrompt()
  const [hiddenUntil, setHiddenUntil] = useState(softHiddenUntil)
  const [open, setOpen] = useState(false)
  const shown = prompt === 'soft' && Date.now() >= hiddenUntil
  usePromptShown('soft', shown)
  if (!shown && !open) return null
  return (
    <>
      {shown && (
        <div
          data-testid="signin-soft"
          className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1"
        >
          <Text as="p" level="body-small" className="text-content-layout-2">
            {SOFT_PROMPT}
          </Text>
          <Button
            variant="rising"
            modifier="ghost"
            size="small"
            label="Sign in"
            onClick={() => setOpen(true)}
          />
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Not now"
            onClick={() => {
              hideSoft()
              setHiddenUntil(softHiddenUntil())
            }}
          />
        </div>
      )}
      <AccountLoginDialog
        isOpen={open}
        onClose={() => setOpen(false)}
        onSuccess={() => setOpen(false)}
      />
    </>
  )
}

/** Most of the free allowance is used: a card on Home and the Inbox until the user signs in. */
export function SigninBudgetCard({
  compact = false,
  className,
}: {
  /** Stacks the button under the text, for a narrow column. */
  compact?: boolean
  className?: string
}) {
  const { prompt, account } = usePrompt()
  const [open, setOpen] = useState(false)
  const shown = prompt === 'budget'
  usePromptShown('budget', shown)
  if (!shown) return null
  return (
    <section
      aria-label="Sign in"
      data-testid="signin-budget"
      className={cn(
        'flex gap-4 rounded-card border border-border-layout-2 bg-surface-layout-1 px-5 py-4',
        compact ? 'flex-col items-start' : 'flex-wrap items-center',
        className
      )}
    >
      <div className="min-w-0 flex-1">
        <Text as="h2" level="body-medium" className="text-content-layout-1">
          {BUDGET_PROMPT}
        </Text>
        <Text as="p" level="caption" className="mt-0.5 text-content-layout-3">
          {usedLine(account)} Sign in and AI checks keep going, with your
          history carried over.
        </Text>
      </div>
      <Button
        variant="rising"
        modifier="solid"
        label="Sign in"
        onClick={() => setOpen(true)}
      />
      <AccountLoginDialog
        isOpen={open}
        onClose={() => setOpen(false)}
        onSuccess={() => setOpen(false)}
      />
    </section>
  )
}

/** Why the free allowance ended: used up, or past its days. */
function endedLine(account: AccountStatusResponse | undefined): string {
  if ((account?.percent_used ?? 0) >= 100)
    return "You've used the free AI that comes with RDST on this computer."
  const days = account?.trial_days
  return days
    ? `Your ${days} days of free AI on this computer are up.`
    : 'The free AI on this computer has ended.'
}

/**
 * The free allowance has ended: the blocking sign-in step in place of the page. Charts,
 * alerts and the Inbox keep running behind it.
 */
export function SigninRequiredStep({
  onSignIn,
  onUseKey,
}: {
  onSignIn: () => void
  onUseKey: () => void
}) {
  const { account } = usePrompt()
  usePromptShown('required', true)
  return (
    <div className="max-w-2xl" data-testid="signin-required">
      <Text as="h2" level="headline-2" className="text-content-layout-1">
        {BUDGET_PROMPT}
      </Text>
      <Text as="p" level="body-medium" className="mt-3 text-content-layout-3">
        {endedLine(account)} Sign in to keep AI checks, deep analysis and Ask
        going. Your databases, history and settings carry over, and charts and
        alerts keep running either way.
      </Text>
      <div className="mt-8 flex flex-wrap items-center gap-3">
        <Button
          variant="rising"
          modifier="solid"
          size="large"
          label="Sign in"
          onClick={onSignIn}
          data-testid="signin-required-action"
        />
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Use my own Anthropic key instead"
          onClick={onUseKey}
        />
      </div>
      <SignInTrustNote className="mt-8" />
    </div>
  )
}
