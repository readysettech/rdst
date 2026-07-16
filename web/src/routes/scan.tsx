/**
 * Scan page — Scan codebase for ORM queries
 * Two-step flow: Step 1 = configure & start, Step 2 = progress & results
 */

import { createFileRoute } from '@tanstack/react-router';
import { useState, useCallback } from 'react';
import { Button } from '@rs/ui-new/button';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { TargetLockNotice } from '../components';
import { useTarget } from '../hooks/useTarget';
import { useRecentScanDirs } from '../hooks/useRecentScanDirs';
import { useScan } from '../lib/useScan';
import { useTargetPasswordLock } from '../lib/useTargetPasswordLock';
import { useCacheAction } from '../lib/useCacheAction';
import {
  ScanHeader,
  ScanFilters,
  ScanProgress,
  ScanSummaryPanel,
  ScanResultsTable,
  ScanAnalysisTable,
} from '../components/scan';

export const Route = createFileRoute('/scan')({
  component: ScanPage,
});

function ScanPage() {
  const { target } = useTarget();
  const { recentDirs, addRecentDir } = useRecentScanDirs();
  const passwordLock = useTargetPasswordLock(target);

  // Cache integration
  const { cacheQuery, cachingId: cachingHash } = useCacheAction({ target });

  const handleCacheQuery = useCallback((sql: string, id: string) => {
    cacheQuery(sql, id);
  }, [cacheQuery]);

  // Filter state
  const [directory, setDirectory] = useState('');
  const [analyze, setAnalyze] = useState(true);
  const [shallow, setShallow] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [diff, setDiff] = useState('');
  const [filePattern, setFilePattern] = useState('');
  const [nosave, setNosave] = useState(false);
  const [check, setCheck] = useState(false);
  const [warnThreshold, setWarnThreshold] = useState(60);
  const [failThreshold, setFailThreshold] = useState(40);
  const [scanTarget, setScanTarget] = useState<string | null>(null);

  // Hook state
  const {
    startScan,
    cancel,
    reset,
    state,
    phase,
    phaseProgress,
    statusMessage,
    queries,
    summary,
    error,
  } = useScan();

  const handleStart = useCallback(() => {
    if (!target || passwordLock.isLocked || !directory.trim()) return;

    setScanTarget(target);
    addRecentDir(directory.trim());
    startScan(target, directory.trim(), {
      analyze,
      shallow,
      dry_run: dryRun,
      diff: diff.trim() || undefined,
      check,
      warn_threshold: warnThreshold,
      fail_threshold: failThreshold,
      file_pattern: filePattern.trim() || undefined,
      nosave,
    });
  }, [
    target,
    passwordLock.isLocked,
    directory,
    analyze,
    shallow,
    dryRun,
    diff,
    check,
    warnThreshold,
    failThreshold,
    filePattern,
    nosave,
    addRecentDir,
    startScan,
  ]);

  const handleNewScan = useCallback(() => {
    setScanTarget(null);
    reset();
  }, [reset]);

  const isStep1 = state === 'idle';

  // Collect active option tags for the context bar
  const optionTags: { label: string; variant: 'informative' | 'primary' | 'warning' }[] = [];
  if (analyze) optionTags.push({ label: shallow ? 'shallow analyze' : 'analyze', variant: 'primary' });
  if (dryRun) optionTags.push({ label: 'dry-run', variant: 'informative' });
  if (diff.trim()) optionTags.push({ label: `diff: ${diff.trim()}`, variant: 'informative' });
  if (check) optionTags.push({ label: 'CI check', variant: 'warning' });
  if (nosave) optionTags.push({ label: 'no-save', variant: 'informative' });
  if (filePattern.trim()) optionTags.push({ label: filePattern.trim(), variant: 'informative' });

  return (
    <div className="space-y-6 w-full">
      <ScanHeader state={state} queriesCount={queries.length} />

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

      <AnimatePresence mode="wait">
        {isStep1 ? (
          <m.div
            key="step-1"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3 }}
          >
            <ScanFilters
              directory={directory}
              setDirectory={setDirectory}
              recentDirs={recentDirs}
              analyze={analyze}
              setAnalyze={setAnalyze}
              shallow={shallow}
              setShallow={setShallow}
              dryRun={dryRun}
              setDryRun={setDryRun}
              diff={diff}
              setDiff={setDiff}
              filePattern={filePattern}
              setFilePattern={setFilePattern}
              nosave={nosave}
              setNosave={setNosave}
              check={check}
              setCheck={setCheck}
              warnThreshold={warnThreshold}
              setWarnThreshold={setWarnThreshold}
              failThreshold={failThreshold}
              setFailThreshold={setFailThreshold}
              state={state}
              onStart={handleStart}
              onCancel={cancel}
              hasTarget={!!target && !passwordLock.isLocked}
            />
          </m.div>
        ) : (
          <m.div
            key="step-2"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3 }}
            className="space-y-5"
          >
            {/* Compact context bar */}
            <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-xl bg-surface-layout-2/40 border border-border-layout-1">
              <HStack className="gap-2.5 items-center min-w-0 flex-wrap">
                <HStack className="gap-2 items-center min-w-0 shrink">
                  <Icon name="folder-file" label="Directory" className="w-3.5 h-3.5 text-content-layout-3 shrink-0" />
                  <Text level="mono-small" className="text-content-layout-1 truncate">
                    {directory.trim()}
                  </Text>
                </HStack>
                {optionTags.map((tag) => (
                  <Tag
                    key={tag.label}
                    size="small"
                    variant={tag.variant}
                    modifier="ghost"
                    label={tag.label}
                  />
                ))}
              </HStack>
              <div className="shrink-0">
                {state === 'scanning' ? (
                  <Button
                    variant="negative"
                    modifier="ghost"
                    size="small"
                    label="Cancel"
                    icon="close"
                    iconPosition="left"
                    onClick={cancel}
                  />
                ) : (
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    label="New Scan"
                    icon="search"
                    iconPosition="left"
                    onClick={handleNewScan}
                  />
                )}
              </div>
            </div>

            {state === 'scanning' && (
              <ScanProgress
                phase={phase}
                phaseProgress={phaseProgress}
                statusMessage={statusMessage}
              />
            )}

            {error !== null && (
              <div className="bg-surface-negative-soft rounded-xl border border-border-negative p-4">
                <Text level="body-small" className="text-content-negative-soft">
                  Error: {error}
                </Text>
              </div>
            )}

            {summary && <ScanSummaryPanel summary={summary} />}

            <ScanResultsTable
              queries={queries}
              state={state}
              target={target}
              onCacheQuery={handleCacheQuery}
              cachingHash={cachingHash}
            />

            {summary?.analysis && <ScanAnalysisTable analysis={summary.analysis} scanTarget={scanTarget} />}
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}
