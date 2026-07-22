import { useEffect, useState } from 'react';
import { Icon } from '@rs/ui-new/icon';
import { HStack } from '@rs/ui-new/stack';
import { Text } from '@rs/ui-new/text';
import { useQueryClient } from '@tanstack/react-query';

import {
  dismissBootstrapRun,
  reattachBootstrapRun,
  useBootstrapRunState,
} from '../lib/bootstrapRun';
import { invalidateTrialRelatedQueries } from '../lib/trialQueries';
import { TrialRegistrationDialog } from './TrialRegistrationDialog';

/**
 * Sidebar pill for the add-database bootstrap run. Renders nothing when no
 * run is active; reattaches to a stored run on mount so a reload resumes
 * progress. On needs_key it becomes the non-blocking key coax: clicking
 * opens the trial dialog (which also offers the own-key path); the backend
 * resumes annotation by itself once a key lands.
 */
export function BootstrapChip() {
  const run = useBootstrapRunState();
  const queryClient = useQueryClient();
  const [trialOpen, setTrialOpen] = useState(false);

  useEffect(() => {
    reattachBootstrapRun();
  }, []);

  if (run.status === 'idle') return null;

  return (
    <>
      {run.status === 'needs_key' && (
        <button
          type="button"
          onClick={() => setTrialOpen(true)}
          className="w-full rounded-lg bg-surface-warning-soft/20 px-3 py-2 text-left cursor-pointer"
          data-testid="bootstrap-chip"
        >
          <HStack className="gap-2 items-center">
            <Icon
              name="alert"
              label="Key needed"
              className="w-3.5 h-3.5 text-content-warning-soft shrink-0"
            />
            <Text level="caption" className="text-content-warning-soft">
              Add an AI key to finish setup
            </Text>
          </HStack>
        </button>
      )}

      {run.status === 'running' && (
        <div
          className="rounded-lg bg-surface-primary-soft/20 px-3 py-2"
          data-testid="bootstrap-chip"
        >
          <HStack className="gap-2 items-center">
            <span className="w-3 h-3 rounded-full border-2 border-content-primary-soft border-t-transparent animate-spin shrink-0" />
            <div className="min-w-0">
              <Text level="caption" className="truncate text-content-primary-soft">
                {run.target ? `Setting up ${run.target}` : 'Setting up database'}
              </Text>
              {run.message && (
                <Text level="caption" className="truncate text-content-layout-3">
                  {run.message}
                </Text>
              )}
            </div>
          </HStack>
        </div>
      )}

      {run.status !== 'needs_key' && run.status !== 'running' && (
        // Terminal states: one calm line, click to dismiss.
        <button
          type="button"
          onClick={dismissBootstrapRun}
          className={`w-full rounded-lg px-3 py-2 text-left cursor-pointer ${
            run.status === 'done'
              ? 'bg-surface-positive-soft/20'
              : 'bg-surface-negative-soft/20'
          }`}
          title="Dismiss"
          data-testid="bootstrap-chip"
        >
          <HStack className="gap-2 items-center">
            <Icon
              name={run.status === 'done' ? 'tick' : 'alert'}
              label={run.status === 'done' ? 'Ready' : 'Setup issue'}
              className={`w-3.5 h-3.5 shrink-0 ${
                run.status === 'done'
                  ? 'text-content-positive-soft'
                  : 'text-content-negative-soft'
              }`}
            />
            <Text
              level="caption"
              className={
                run.status === 'done'
                  ? 'text-content-positive-soft'
                  : 'text-content-negative-soft'
              }
            >
              {run.status === 'done'
                ? `${run.target ?? 'Database'} is ready`
                : run.message || 'Setup did not finish'}
            </Text>
          </HStack>
        </button>
      )}

      {/* Sibling of the state branches so a key landing (which flips the run
          back to running) cannot unmount the dialog mid-flow. */}
      <TrialRegistrationDialog
        isOpen={trialOpen}
        onClose={() => setTrialOpen(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient);
          setTrialOpen(false);
        }}
      />
    </>
  );
}
