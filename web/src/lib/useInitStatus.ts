import { useQuery } from '@tanstack/react-query';
import { fetchInitStatus, type InitStatusResponse } from './api';

export function useInitStatus() {
  return useQuery<InitStatusResponse>({
    queryKey: ['init-status'],
    queryFn: fetchInitStatus,
    staleTime: 30_000,
    retry: 1,
  });
}
