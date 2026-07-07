import { useQuery } from '@tanstack/react-query';
import { fetchBrowse, type BrowseResponse } from './api';

export function useBrowse(path: string | undefined, enabled: boolean, ext?: string) {
  return useQuery<BrowseResponse>({
    queryKey: ['browse', path, ext ?? null],
    queryFn: () => fetchBrowse(path, ext),
    enabled,
    staleTime: 30_000,
  });
}
