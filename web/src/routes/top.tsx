/**
 * Top Queries page - Monitor and analyze slow queries
 */

import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Text } from '@rs/ui-new/text';
import { Show } from '@rs/ui-new/show';
import { TargetLockNotice } from '../components';
import { useTarget } from '../hooks/useTarget';
import { useTop } from '../lib/useTop';
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock';
import { addQueryToRegistry } from '../lib/api';
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
  const queryClient = useQueryClient();
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
      // Check if query has parameters that need substitution
      if (hasParameters(query.query_text)) {
        setParamDialogQuery(query.query_text);
        return;
      }

      navigate({
        to: '/results',
        search: {
          query: query.query_text,
          target: target || undefined,
        },
      });
    },
    [passwordLock.isLocked, navigate, target]
  );

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

    for (const query of queries) {
      if (!savedHashes.has(query.query_hash)) {
        try {
          await addQueryToRegistry(query.query_text, target);
        } catch (err) {
          console.error('Failed to save query:', err);
        }
      }
    }
    queryClient.invalidateQueries({ queryKey: ['queryRegistry'] });
  }, [target, queries, savedHashes, queryClient]);

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
        onClose={() => setParamDialogQuery(null)}
        onSubmit={handleParamSubmit}
        query={paramDialogQuery || ''}
      />
    </div>
  );
}
