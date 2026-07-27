import { useTrialSource } from './trialQueries'
import { useAnthropicValidity } from './useAnthropicValidity'

export type AiGate =
  | { status: 'checking' }
  | { status: 'ready' }
  /** The probe itself failed (network/provider) — run allowed, badge explains. */
  | { status: 'unverified' }
  | { status: 'blocked'; reason: 'missing' | 'invalid' | 'exhausted' }

/**
 * Resolves the AI-insights dependency up front instead of after a wasted run
 * (H-4). Reuses the cached `useAnthropicValidity` probe gated on key presence
 * so the provider is never pinged without a key. A missing or rejected key
 * blocks the primary run actions — no silent degraded runs.
 */
export function useAiGate(): AiGate {
  const { envRequirements, anthropicRequirement, isTrialSource, trialStatus } =
    useTrialSource()
  const isTrialExhausted =
    anthropicRequirement?.source === 'trial_exhausted' ||
    (isTrialSource &&
      (trialStatus?.status === 'exhausted' || trialStatus?.active === false))
  const hasKey =
    (Boolean(anthropicRequirement?.satisfied) || isTrialSource) &&
    !isTrialExhausted
  const validityQuery = useAnthropicValidity(hasKey)
  const validity = validityQuery.data

  if (!envRequirements) return { status: 'checking' }
  if (isTrialExhausted) return { status: 'blocked', reason: 'exhausted' }
  if (!hasKey) return { status: 'blocked', reason: 'missing' }
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
