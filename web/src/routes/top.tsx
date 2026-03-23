/**
 * Top Queries page - Monitor and analyze slow queries
 */

import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useState, useCallback, useMemo } from 'react';
import { Text } from '@rs/ui-new/text';
import { Show } from '@rs/ui-new/show';
import { TargetLockNotice } from '../components';
import { useTarget } from '../hooks/useTarget';
import { useTop } from '../lib/useTop';
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock';
import { useQueryRegistry } from '../lib/useQueryRegistry';
import {
  TopFilters,
  TopHeader,
  TopQueryTable,
  TopStatus,
  ParameterDialog,
  hasParameters,
} from '../components/top';
import type { TopMode, TopQuery } from '../types/top';

export const Route = createFileRoute('/top')({
  component: TopPage,
});

function TopPage() {
  const navigate = useNavigate();
  const { queries: registryQueries, addMutation: addQueryMutation } = useQueryRegistry();
  const { target } = useTarget();
  const passwordLock = useTargetPasswordLock(target);

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
