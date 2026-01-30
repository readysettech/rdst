import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchQueryRegistry, addQueryToRegistry, removeQueryFromRegistry, updateQueryTag, type QueryRegistryEntry } from './api';

export type { QueryRegistryEntry };

export function useQueryRegistry() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['queryRegistry'],
    queryFn: () => fetchQueryRegistry(50),
    staleTime: 30 * 1000,
  });

  const addMutation = useMutation({
    mutationFn: ({ sql, target }: { sql: string; target?: string }) => addQueryToRegistry(sql, target),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (hash: string) => removeQueryFromRegistry(hash),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
    },
  });

  const updateTagMutation = useMutation({
    mutationFn: ({ hash, tag }: { hash: string; tag: string }) => updateQueryTag(hash, tag),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
    },
  });

  const addQuery = (sql: string, target?: string) => {
    addMutation.mutate({ sql, target });
  };

  const removeQuery = (hash: string) => {
    removeMutation.mutate(hash);
  };

  const updateTag = (hash: string, tag: string) => {
    updateTagMutation.mutate({ hash, tag });
  };

  return {
    queries: data?.queries || [],
    isLoading,
    addQuery,
    removeQuery,
    updateTag,
  };
}
