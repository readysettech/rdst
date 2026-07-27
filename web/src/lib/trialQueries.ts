import { QueryClient } from '@tanstack/react-query';
import { useEnvRequirements } from './useEnvRequirements';
import { useTrialStatus } from './useTrialStatus';

// Everything the AI gate reads: which key is configured, whether it actually
// authenticates, and how much trial credit is left. The validity verdict is
// cached with a long staleTime, so a stale "no key"/"rejected" answer outlives
// the key that produced it unless it is invalidated alongside the others.
const AI_GATE_QUERY_KEYS = [
  ['env-requirements'],
  ['anthropic-validity'],
  ['trial-status'],
];

export async function invalidateAiGateQueries(queryClient: QueryClient) {
  await Promise.all(
    AI_GATE_QUERY_KEYS.map((queryKey) =>
      queryClient.invalidateQueries({ queryKey, refetchType: 'all' }),
    ),
  );
}

export async function invalidateTrialRelatedQueries(queryClient: QueryClient) {
  const options = { refetchType: 'all' as const };
  await Promise.all([
    invalidateAiGateQueries(queryClient),
    queryClient.invalidateQueries({ queryKey: ['status'], ...options }),
    queryClient.invalidateQueries({ queryKey: ['init-status'], ...options }),
    // Activating a trial promotes its email to the machine identity; refresh
    // the sidebar identity so it shows the current address, not a stale one.
    queryClient.invalidateQueries({ queryKey: ['settings', 'email'], ...options }),
  ]);
}

export function isTrialBasedSource(source: string | undefined): boolean {
  return source === 'trial' || source === 'trial_exhausted';
}

export function useTrialSource() {
  const { data: envRequirements } = useEnvRequirements();

  const anthropicRequirement = envRequirements?.requirements.find(
    (r) => r.kind === 'anthropic_api_key',
  );
  const anthropicSource = anthropicRequirement?.source;
  const isTrialSource = isTrialBasedSource(anthropicSource ?? undefined);

  const { data: trialStatus } = useTrialStatus({ enabled: isTrialSource });

  return {
    envRequirements,
    anthropicRequirement,
    anthropicSource,
    isTrialSource,
    trialStatus,
  };
}

