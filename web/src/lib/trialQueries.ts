import type { QueryClient } from '@tanstack/react-query'
import type { TrialStatusResponse } from './api'
import { useEnvRequirements } from './useEnvRequirements'

// Everything the AI gate reads. Legacy trial keys remain in the invalidation
// set only so old installations cannot retain a stale accepted result.
const AI_GATE_QUERY_KEYS = [
  ['env-requirements'],
  ['anthropic-validity'],
  ['trial-status'],
  ['account-status'],
]

export async function invalidateAiGateQueries(queryClient: QueryClient) {
  await Promise.all(
    AI_GATE_QUERY_KEYS.map((queryKey) =>
      queryClient.invalidateQueries({ queryKey, refetchType: 'all' })
    )
  )
}

export async function invalidateTrialRelatedQueries(queryClient: QueryClient) {
  const options = { refetchType: 'all' as const }
  await Promise.all([
    invalidateAiGateQueries(queryClient),
    queryClient.invalidateQueries({ queryKey: ['status'], ...options }),
    queryClient.invalidateQueries({ queryKey: ['init-status'], ...options }),
    // Account sign-in may update the machine identity shown in the sidebar.
    queryClient.invalidateQueries({
      queryKey: ['settings', 'email'],
      ...options,
    }),
  ])
}

export function isTrialBasedSource(source: string | undefined): boolean {
  return source === 'trial' || source === 'trial_exhausted'
}

function disabledLegacyTrialStatus(): TrialStatusResponse | undefined {
  return undefined
}

export function useTrialSource() {
  const envRequirementsQuery = useEnvRequirements()
  const { data: envRequirements } = envRequirementsQuery

  const anthropicRequirement = envRequirements?.requirements.find(
    (r) => r.kind === 'anthropic_api_key'
  )
  const anthropicSource = anthropicRequirement?.source
  const isTrialSource = isTrialBasedSource(anthropicSource ?? undefined)
  const trialStatus = disabledLegacyTrialStatus()

  return {
    envRequirements,
    envRequirementsQuery,
    anthropicRequirement,
    anthropicSource,
    isTrialSource,
    trialStatus,
  }
}
