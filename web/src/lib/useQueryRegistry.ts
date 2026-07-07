import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { fetchQueryRegistry, addQueryToRegistry, removeQueryFromRegistry, updateQueryTag, updateQuerySql, importQueries, type QueryRegistryEntry, type ImportQueriesResponse } from './api';

export type { QueryRegistryEntry };

export function useQueryRegistry(initialLimit = 100) {
  const queryClient = useQueryClient();
  const [limit, setLimit] = useState(initialLimit);
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['queryRegistry', limit, offset],
    queryFn: () => fetchQueryRegistry(limit, offset),
    staleTime: 30 * 1000,
    placeholderData: keepPreviousData,
  });
  const queries = data?.queries ?? [];
  const total = data?.total ?? 0;
  const listError = data?.error ?? null;

  useEffect(() => {
    if (total > 0 && offset >= total) {
      setOffset(Math.max(total - limit, 0));
    }
  }, [total, offset, limit]);

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

  const updateSqlMutation = useMutation({
    mutationFn: ({ hash, sql }: { hash: string; sql: string }) =>
      updateQuerySql(hash, sql).then((result) => {
        if (!result.success) {
          throw new Error(result.error || 'Failed to update SQL');
        }
        return result;
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
    },
  });

  const importMutation = useMutation<
    ImportQueriesResponse,
    Error,
    { file: string; update?: boolean; target?: string }
  >({
    mutationFn: ({ file, update, target }) => importQueries(file, { update, target }),
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

  const nextPage = useCallback(() => {
    setOffset((prev) => prev + limit);
  }, [limit]);

  const prevPage = useCallback(() => {
    setOffset((prev) => Math.max(prev - limit, 0));
  }, [limit]);

  const resetPagination = useCallback(() => {
    setOffset(0);
  }, []);

  return {
    queries,
    isLoading,
    isFetching,
    total,
    listError,
    limit,
    offset,
    setLimit,
    setOffset,
    nextPage,
    prevPage,
    resetPagination,
    addQuery,
    addMutation,
    removeQuery,
    updateTag,
    updateSqlMutation,
    importMutation,
  };
}
