import { QueryClient } from '@tanstack/react-query';
import { useEnvRequirements } from './useEnvRequirements';
import { useTrialStatus } from './useTrialStatus';

export async function invalidateTrialRelatedQueries(queryClient: QueryClient) {
  const options = { refetchType: 'all' as const };
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['status'], ...options }),
    queryClient.invalidateQueries({ queryKey: ['init-status'], ...options }),
    queryClient.invalidateQueries({ queryKey: ['env-requirements'], ...options }),
    queryClient.invalidateQueries({ queryKey: ['trial-status'], ...options }),
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

