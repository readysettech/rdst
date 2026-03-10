import { QueryClient, useQuery } from '@tanstack/react-query';
import { fetchEnvRequirements, fetchTrialStatus } from './api';

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
  const { data: envRequirements } = useQuery({
    queryKey: ['env-requirements'],
    queryFn: fetchEnvRequirements,
    staleTime: 30000,
    retry: 1,
  });

  const anthropicRequirement = envRequirements?.requirements.find(
    (r) => r.kind === 'anthropic_api_key',
  );
  const anthropicSource = anthropicRequirement?.source;
  const isTrialSource = isTrialBasedSource(anthropicSource);

  const { data: trialStatus } = useQuery({
    queryKey: ['trial-status'],
    queryFn: fetchTrialStatus,
    staleTime: 30000,
    retry: 1,
    enabled: isTrialSource,
  });

  return {
    envRequirements,
    anthropicRequirement,
    anthropicSource,
    isTrialSource,
    trialStatus,
  };
}

