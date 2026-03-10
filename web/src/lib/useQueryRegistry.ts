import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchQueryRegistry, addQueryToRegistry, removeQueryFromRegistry, updateQueryTag, type QueryRegistryEntry } from './api';

export type { QueryRegistryEntry };

export function useQueryRegistry() {
  const queryClient = useQueryClient();

  const { data: queries = [], isLoading } = useQuery({
    queryKey: ['queryRegistry', 50],
    queryFn: () => fetchQueryRegistry(50),
    select: (data) => data.queries,
    staleTime: 30 * 1000,
  });

  const addMutation = useMutation({
    mutationFn: ({ sql, target }: { sql: string; target?: string }) =>
      addQueryToRegistry(sql, target).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to add query');
        }
        return result;
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (hash: string) =>
      removeQueryFromRegistry(hash).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to remove query');
        }
        return result;
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
    },
  });

  const updateTagMutation = useMutation({
    mutationFn: ({ hash, tag }: { hash: string; tag: string }) =>
      updateQueryTag(hash, tag).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to update query tag');
        }
        return result;
      }),
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
    queries,
    isLoading,
    addQuery,
    addMutation,
    removeQuery,
    updateTag,
  };
}
