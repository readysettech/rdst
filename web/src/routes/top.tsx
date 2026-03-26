/**
 * Top Queries page - Monitor and analyze slow queries
 */

import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useState, useCallback, useMemo } from 'react';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { CopyButton } from '@rs/ui-new/copy-button';
import { TargetLockNotice } from '../components';
import { useTarget } from '../hooks/useTarget';
import { useTop } from '../lib/useTop';
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock';
import { useQueryRegistry } from '../lib/useQueryRegistry';
import { useCacheAction } from '../lib/useCacheAction';
import {
  TopFilters,
  TopHeader,
  TopQueryTable,
  TopStatus,
  ParameterDialog,
  hasParameters,
} from '../components/top';
import type { TopDbLimitWarningEventData, TopMode, TopQuery } from '../types/top';

export const Route = createFileRoute('/top')({
  component: TopPage,
});

function formatKB(bytes: number) {
  return `${Math.round(bytes / 1024)} KB`;
}

function DbLimitWarning({ warning: w }: { warning: TopDbLimitWarningEventData }) {
  const isPostgres = w.db_engine.includes('postgres');
  const sql = isPostgres
    ? `ALTER SYSTEM SET ${w.setting_name} = ${w.recommended_bytes};`
    : `SET GLOBAL ${w.setting_name} = ${w.recommended_bytes};`;

  return (
    <div className="bg-surface-warning-soft/50 rounded-xl border border-border-warning-soft p-5">
      <HStack className="gap-3 items-start">
        <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center shrink-0 mt-0.5">
          <Icon name="alert" label="Warning" className="w-4 h-4 text-content-warning-soft" />
        </div>
        <VStack className="gap-2 items-start flex-1 min-w-0">
          <VStack className="gap-0.5 items-start">
            <Text level="label-small" className="text-content-warning-soft">
              Low Database Query Size Limit
            </Text>
            <Text level="body-small" className="text-content-layout-2">
              <code className="font-mono">{w.setting_name}</code> is set
              to {formatKB(w.db_limit_bytes)}. Increase to at least {formatKB(w.recommended_bytes)} to
              avoid query truncation.
            </Text>
          </VStack>
          <div className="w-full relative group">
            <pre className="px-3 py-2.5 rounded-lg bg-surface-layout-1 text-xs font-mono text-content-layout-1 overflow-x-auto">
              {sql}{isPostgres ? '\n-- Then restart PostgreSQL' : ''}
            </pre>
            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton value={sql} />
            </div>
          </div>
        </VStack>
      </HStack>
    </div>
  );
}

export function TopPage() {
  const navigate = useNavigate();
  const { queries: registryQueries, addMutation: addQueryMutation } = useQueryRegistry();
  const { target } = useTarget();
  const passwordLock = useTargetPasswordLock(target);

  // Cache integration
  const { cacheQuery, cachingId: cachingHash, isCached } = useCacheAction({ target });

  const handleCacheQuery = useCallback((query: TopQuery) => {
    cacheQuery(query.query_text, query.query_hash);
  }, [cacheQuery]);

  // Filter state
  const [mode, setMode] = useState<TopMode>('historical');
  const [source, setSource] = useState('auto');
  const [sort, setSort] = useState('total_time');
  const [limit, setLimit] = useState(10);
  const [filterPattern, setFilterPattern] = useState('');
  const [duration, setDuration] = useState(0);
  const [autoSave, setAutoSave] = useState(true);

  // Parameter dialog state
  const [paramDialogQuery, setParamDialogQuery] = useState<string | null>(null);
  const [paramDialogStoredParams, setParamDialogStoredParams] = useState<Record<string, string | number> | undefined>(undefined);

  // Build a lookup from query hash to registry entry for stored params
  const registryParamsByHash = useMemo(() => {
    const map = new Map<string, Record<string, string | number>>();
    for (const entry of registryQueries) {
      if (entry.most_recent_params && Object.keys(entry.most_recent_params).length > 0) {
        map.set(entry.hash, entry.most_recent_params);
      }
    }
    return map;
  }, [registryQueries]);

  // Hook state
  const {
    getTop,
    startRealtime,
    stopRealtime,
    reset,
    state,
    queries,
    connectionInfo,
    sourceFallback,
    dbLimitWarning,
    runtimeSeconds,
    totalTracked,
    newlySaved,
    savedHashes,
    error,
  } = useTop();

  const handleStart = useCallback(() => {
    if (!target || passwordLock.isLocked) return;

    if (mode === 'realtime') {
      startRealtime(target, {
        limit,
        duration: duration > 0 ? duration : undefined,
        auto_save: autoSave,
      });
    } else {
      getTop(target, {
        limit,
        source: source as 'auto' | 'pg_stat' | 'activity' | 'digest',
        sort: sort as 'total_time' | 'freq' | 'avg_time' | 'load',
        filter_pattern: filterPattern || undefined,
        auto_save: autoSave,
      });
    }
  }, [
    target,
    passwordLock.isLocked,
    mode,
    limit,
    duration,
    autoSave,
    source,
    sort,
    filterPattern,
    startRealtime,
    getTop,
  ]);

  const handleStop = useCallback(() => {
    stopRealtime();
  }, [stopRealtime]);

  const handleAnalyze = useCallback(
    (query: TopQuery) => {
      if (passwordLock.isLocked) return;
      const storedParams = registryParamsByHash.get(query.query_hash);
      // Check if query has parameters that need substitution
      if (hasParameters(query.query_text)) {
        setParamDialogQuery(query.query_text);
        setParamDialogStoredParams(storedParams);
        return;
      }

      navigate({
        to: '/results',
        search: {
          query: query.query_text,
          target: target || undefined,
          params: storedParams ? JSON.stringify(storedParams) : undefined,
        },
      });
    },
    [passwordLock.isLocked, navigate, target, registryParamsByHash]
  );

  const handleParamDialogClose = useCallback(() => {
    setParamDialogQuery(null);
    setParamDialogStoredParams(undefined);
  }, []);

  const handleParamSubmit = useCallback(
    (substitutedQuery: string) => {
      setParamDialogQuery(null);
      navigate({
        to: '/results',
        search: {
          query: substitutedQuery,
          target: target || undefined,
        },
      });
    },
    [navigate, target]
  );

  const handleSaveAll = useCallback(async () => {
    if (!target || queries.length === 0) return;

    const unsavedQueries = queries.filter((query) => !savedHashes.has(query.query_hash));

    const saves = unsavedQueries.map((query) =>
      addQueryMutation.mutateAsync({
        sql: query.query_text,
        target,
      }),
    );

    await Promise.allSettled(saves);
  }, [target, queries, savedHashes, addQueryMutation]);

  // Reset when mode changes
  const handleModeChange = useCallback(
    (newMode: TopMode) => {
      reset();
      setMode(newMode);
    },
    [reset]
  );

  return (
    <div className="space-y-6 w-full">
      <TopHeader
        state={state}
        queriesCount={queries.length}
        onSaveAll={handleSaveAll}
      />

      <Show when={!target}>
        <div className="bg-surface-warning-soft rounded-xl border border-border-warning p-4">
          <Text level="body-small" className="text-content-warning-soft">
            Please select a target database from the sidebar to continue.
          </Text>
        </div>
      </Show>
      <Show when={passwordLock.isLocked}>
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      </Show>

      <TopFilters
        mode={mode}
        setMode={handleModeChange}
        source={source}
        setSource={setSource}
        sort={sort}
        setSort={setSort}
        limit={limit}
        setLimit={setLimit}
        filterPattern={filterPattern}
        setFilterPattern={setFilterPattern}
        duration={duration}
        setDuration={setDuration}
        autoSave={autoSave}
        setAutoSave={setAutoSave}
        state={state}
        onStart={handleStart}
        onStop={handleStop}
        hasTarget={!!target && !passwordLock.isLocked}
      />

      <Show when={state !== 'idle' && state !== 'error'}>
        <TopStatus
          state={state}
          connectionInfo={connectionInfo}
          sourceFallback={sourceFallback}
          runtimeSeconds={runtimeSeconds}
          totalTracked={totalTracked}
          newlySaved={newlySaved}
          isRealtime={mode === 'realtime'}
        />
      </Show>

      {dbLimitWarning && <DbLimitWarning warning={dbLimitWarning} />}

      <Show when={error !== null}>
        <div className="bg-surface-negative-soft rounded-xl border border-border-negative p-4">
          <Text level="body-small" className="text-content-negative-soft">
            Error: {error}
          </Text>
        </div>
      </Show>

      <TopQueryTable
        queries={queries}
        state={state}
        isRealtime={mode === 'realtime'}
        onAnalyze={handleAnalyze}
        onCache={handleCacheQuery}
        cachingHash={cachingHash}
        isCached={isCached}
      />

      <ParameterDialog
        isOpen={paramDialogQuery !== null}
        onClose={handleParamDialogClose}
        onSubmit={handleParamSubmit}
        query={paramDialogQuery || ''}
        initialValues={paramDialogStoredParams}
      />
    </div>
  );
}
