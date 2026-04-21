import { useQuery } from '@tanstack/react-query';
import { fetchTrialStatus, type TrialStatusResponse } from './api';

export function useTrialStatus(options?: { enabled?: boolean }) {
  return useQuery<TrialStatusResponse>({
    queryKey: ['trial-status'],
    queryFn: fetchTrialStatus,
    staleTime: 30_000,
    retry: 1,
    enabled: options?.enabled,
  });
}
