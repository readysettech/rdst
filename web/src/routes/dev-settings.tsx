/**
 * Developer Settings page - Tools and utilities for development mode only.
 * Guarded by import.meta.env.DEV (compile-time constant, tree-shaken in prod).
 */

import { createFileRoute } from '@tanstack/react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Text } from '@rs/ui-new/text';
import { Button } from '@rs/ui-new/button';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { m } from '@rs/ui-new/motion';
import { simulateTrialExhausted, clearKeyring } from '../lib/api';
import { invalidateTrialRelatedQueries, useTrialSource } from '../lib/trialQueries';

export const Route = createFileRoute('/dev-settings')({
  component: DevSettingsPage,
});

function DevSettingsPage() {
  const queryClient = useQueryClient();
  const { isTrialSource, trialStatus } = useTrialSource();

  const isTrialExhausted =
    isTrialSource && (trialStatus?.status === 'exhausted' || trialStatus?.active === false);

  // --- Simulate Trial Exhausted ---
  const simulateTrialMutation = useMutation({
    mutationFn: async () => {
      const result = await simulateTrialExhausted();
      if (!result.success) {
        throw new Error(result.message || 'Failed to simulate trial exhaustion.');
      }
      return {
        ...result,
        message: result.message || 'Trial has been marked as exhausted.',
      };
    },
    onSuccess: () => {
      void invalidateTrialRelatedQueries(queryClient);
    },
  });

  const simulationMessage = simulateTrialMutation.data?.message ?? null;
  const simulationError =
    simulateTrialMutation.error instanceof Error
      ? simulateTrialMutation.error.message
      : null;

  const handleSimulateExhausted = () => {
    if (!isTrialSource) return;
    simulateTrialMutation.reset();
    simulateTrialMutation.mutate();
  };

  // --- Clear Keyring ---
  const clearKeyringMutation = useMutation({
    mutationFn: clearKeyring,
    onSuccess: () => {
      window.location.reload();
    },
  });

  const clearKeyringError =
    clearKeyringMutation.error instanceof Error
      ? clearKeyringMutation.error.message
      : null;

  const handleClearKeyring = () => {
    clearKeyringMutation.reset();
    clearKeyringMutation.mutate();
  };

  // Compile-time constant: in prod builds, Vite inlines `false` and the
  // bundler dead-code-eliminates the rest of the component body.
  if (!import.meta.env.DEV) {
    return null;
  }

  return (
    <div className="space-y-6 w-full">
      {/* Header */}
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-warning-soft flex items-center justify-center">
            <Icon name="test-tube" label="Developer Settings" className="w-6 h-6 text-content-primary-soft" />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Developer Settings
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Tools and utilities available only in development mode
            </Text>
          </VStack>
        </HStack>
      </m.div>

      {/* Simulate Trial Exhausted */}
      <Show when={isTrialSource && !isTrialExhausted}>
        <m.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4 space-y-3">
            <VStack className="gap-2 items-start">
              <HStack className="gap-3 items-center">
                <div className="w-9 h-9 rounded-xl bg-surface-negative-soft flex items-center justify-center">
                  <Icon name="alert" label="Trial" className="w-4 h-4 text-content-negative-soft" />
                </div>
                <VStack className="gap-0.5 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    Simulate Trial Exhausted
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    Marks the current trial as exhausted for testing the exhausted state UI.
                  </Text>
                </VStack>
              </HStack>
              <Show when={!!simulationMessage}>
                <Text level="caption" className="text-content-positive-soft">
                  {simulationMessage}
                </Text>
              </Show>
              <Show when={!!simulationError}>
                <Text level="caption" className="text-content-negative-soft">
                  {simulationError}
                </Text>
              </Show>
              <Button
                variant="negative"
                modifier="outline"
                label="Simulate Exhausted"
                icon="alert"
                iconPosition="left"
                onClick={handleSimulateExhausted}
                loading={simulateTrialMutation.isPending}
                disabled={simulateTrialMutation.isPending}
              />
            </VStack>
          </div>
        </m.div>
      </Show>

      {/* Clear Keyring */}
      <m.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2, delay: 0.05 }}
      >
        <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4 space-y-3">
          <VStack className="gap-2 items-start">
            <HStack className="gap-3 items-center">
              <div className="w-9 h-9 rounded-xl bg-surface-negative-soft flex items-center justify-center">
                <Icon name="trash" label="Clear Keyring" className="w-4 h-4 text-content-negative-soft" />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="label-small" className="text-content-layout-1">
                  Clear Keyring
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Removes stored secrets from the OS keychain, clears local trial state, and reloads the page.
                </Text>
              </VStack>
            </HStack>
            <Show when={!!clearKeyringError}>
              <Text level="caption" className="text-content-negative-soft">
                {clearKeyringError}
              </Text>
            </Show>
            <Button
              variant="negative"
              modifier="outline"
              label="Clear Keyring"
              icon="trash"
              iconPosition="left"
              onClick={handleClearKeyring}
              loading={clearKeyringMutation.isPending}
              disabled={clearKeyringMutation.isPending}
            />
          </VStack>
        </div>
      </m.div>
    </div>
  );
}
