import { useAiConsent } from './aiConsent'
import { useTrialSource } from './trialQueries'
import { useAnthropicValidity } from './useAnthropicValidity'

export type AiGate =
  | { status: 'checking' }
  | { status: 'error'; message: string }
  | { status: 'ready' }
  /** The probe itself failed (network/provider) — run allowed, badge explains. */
  | { status: 'unverified' }
  | { status: 'blocked'; reason: 'missing' | 'invalid' | 'exhausted' }
  /** The user hasn't allowed AI (a build that asks first): nothing goes to AI. */
  | { status: 'off' }

/**
 * Resolves the AI-insights dependency up front instead of after a wasted run
 * (H-4). Reuses the cached `useAnthropicValidity` probe gated on key presence
 * so the provider is never pinged without a key. A missing or rejected key
 * blocks the primary run actions — no silent degraded runs.
 */
export function useAiGate(): AiGate {
  const aiConsent = useAiConsent()
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
  // A key Anthropic rejected stays configured but unsatisfied; the probe names the rejection.
  const hasKey =
    (Boolean(anthropicRequirement?.satisfied) ||
      (anthropicRequirement?.selected_provider === 'claude' &&
        Boolean(anthropicRequirement.anthropic_key_configured))) &&
    !isTrialSource &&
    !isTrialExhausted
  // With AI off, nothing contacts Anthropic, a key check included.
  const validityQuery = useAnthropicValidity(
    hasKey &&
      !isReadysetAccount &&
      !aiConsent.loading &&
      !aiConsent.failed &&
      !aiConsent.off
  )
  const validity = validityQuery.data

  // A build that asks first isn't known to allow AI until its answer loads.
  if (aiConsent.loading) return { status: 'checking' }
  if (aiConsent.off) return { status: 'off' }
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
  // Legacy trial tokens are no longer an AI provider. Existing installations
  // must choose Readyset sign-in or Anthropic BYOK just like a fresh install.
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
