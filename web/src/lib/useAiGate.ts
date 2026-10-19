import { signinPromptOf, useAccountStatus } from './accountStatus'
import { useTrialSource } from './trialQueries'
import { useAnthropicValidity } from './useAnthropicValidity'

export type AiGate =
  | { status: 'checking' }
  | { status: 'error'; message: string }
  | { status: 'ready' }
  /** The probe itself failed (network/provider) — run allowed, badge explains. */
  | { status: 'unverified' }
  | {
      status: 'blocked'
      reason: 'missing' | 'invalid' | 'exhausted' | 'signin_required'
    }

/**
 * Resolves the AI-insights dependency up front instead of after a wasted run
 * (H-4). Reuses the cached `useAnthropicValidity` probe gated on key presence
 * so the provider is never pinged without a key. A missing or rejected key
 * blocks the primary run actions — no silent degraded runs.
 *
 * Without a key, Readyset-hosted AI runs signed in or on the free anonymous
 * allowance, so only an ended allowance (`signin_required`) blocks it.
 */
export function useAiGate(): AiGate {
  const {
    envRequirements,
    envRequirementsQuery,
    anthropicRequirement,
    isTrialSource,
    trialStatus,
  } = useTrialSource()
  const isTrialExhausted =
    anthropicRequirement?.source === 'trial_exhausted' ||
    (isTrialSource &&
      (trialStatus?.status === 'exhausted' || trialStatus?.active === false))
  const isReadysetAccount = anthropicRequirement?.source === 'readyset_account'
  const hasKey =
    Boolean(anthropicRequirement?.satisfied) &&
    !isTrialSource &&
    !isTrialExhausted
  const validityQuery = useAnthropicValidity(hasKey && !isReadysetAccount)
  const validity = validityQuery.data
  const account = useAccountStatus()
  const prompt = signinPromptOf(account.data)
  const ownKey = hasKey && !isReadysetAccount

  if (envRequirementsQuery.isError) {
    return {
      status: 'error',
      message:
        envRequirementsQuery.error instanceof Error
          ? envRequirementsQuery.error.message
          : 'RDST could not check your AI setup.',
    }
  }
  if (!envRequirements) return { status: 'checking' }
  if (!ownKey) {
    if (prompt === 'required')
      return { status: 'blocked', reason: 'signin_required' }
    if (isReadysetAccount && hasKey) return { status: 'ready' }
    if (account.isPending) return { status: 'checking' }
    // The anonymous session is created on first use.
    if (prompt !== null) return { status: 'ready' }
  }
  // A backend without free hosted AI: legacy trial tokens are no longer an AI
  // provider, so these installations choose Readyset sign-in or Anthropic BYOK.
  if (isTrialSource) return { status: 'blocked', reason: 'missing' }
  if (isTrialExhausted) return { status: 'blocked', reason: 'exhausted' }
  if (!hasKey) return { status: 'blocked', reason: 'missing' }
  if (isReadysetAccount) return { status: 'ready' }
  if (validityQuery.isError) return { status: 'unverified' }
  if (!validity) return { status: 'checking' }
  if (validity.valid) return { status: 'ready' }
  if (
    validity.reason === 'exhausted' ||
    validity.source === 'trial_exhausted'
  ) {
    return { status: 'blocked', reason: 'exhausted' }
  }
  if (validity.reason === 'provider_error') return { status: 'unverified' }
  return { status: 'blocked', reason: 'invalid' }
}
