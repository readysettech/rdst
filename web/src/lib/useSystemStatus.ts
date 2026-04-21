import { useQuery } from '@tanstack/react-query';
import { fetchStatus, type StatusResponse } from './api';

export function useSystemStatus() {
  return useQuery<StatusResponse>({
    queryKey: ['status'],
    queryFn: fetchStatus,
    staleTime: 30_000,
    retry: 1,
  });
}
