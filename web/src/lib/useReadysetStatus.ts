import { useQuery } from '@tanstack/react-query';
import { fetchReadysetStatus, type ReadysetContainerStatus } from './api';

export function useReadysetStatus(target: string | null | undefined, options?: { enabled?: boolean }) {
  return useQuery<ReadysetContainerStatus>({
    queryKey: ['readyset-status', target ?? null],
    queryFn: () => fetchReadysetStatus(target || undefined),
    staleTime: 30_000,
    refetchInterval: 60_000,
    enabled: options?.enabled,
  });
}
