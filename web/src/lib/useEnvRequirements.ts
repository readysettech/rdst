import { useQuery } from '@tanstack/react-query';
import { fetchEnvRequirements, type EnvRequirementsResponse } from './api';

export function useEnvRequirements() {
  return useQuery<EnvRequirementsResponse>({
    queryKey: ['env-requirements'],
    queryFn: fetchEnvRequirements,
    staleTime: 30_000,
    retry: 1,
  });
}
