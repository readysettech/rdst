import { useQuery } from '@tanstack/react-query';
import { fetchBrowse, type BrowseResponse } from './api';

export function useBrowse(path: string | undefined, enabled: boolean) {
  return useQuery<BrowseResponse>({
    queryKey: ['browse', path],
    queryFn: () => fetchBrowse(path),
    enabled,
    staleTime: 30_000,
  });
}
