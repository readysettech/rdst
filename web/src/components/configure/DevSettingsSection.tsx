/**
 * Developer settings — the tools formerly on the standalone `/dev-settings`
 * route, merged into the Settings page as its last section (owner decision:
 * "Dev Settings => Settings içine taşınmalı"). Two destructive-ish dev tools
 * (Simulate Trial Exhausted, Clear Keyring), each an elevated card with an
 * icon + label + description, matching the other Settings sections.
 *
 * DEV-only: the caller gates this behind `import.meta.env.DEV` (a compile-time
 * constant Vite inlines to `false` in prod, so the whole subtree — and its
 * imports — dead-code-eliminate out of production builds). [USE-097, VIS-113]
 */

import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { Show } from '@rs/ui-new/show'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { clearKeyring, simulateTrialExhausted } from '../../lib/api'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../../lib/trialQueries'

export function DevSettingsSection() {
  const queryClient = useQueryClient()
  const { isTrialSource, trialStatus } = useTrialSource()
  const [clearKeyringOpen, setClearKeyringOpen] = useState(false)

  const isTrialExhausted =
    isTrialSource &&
    (trialStatus?.status === 'exhausted' || trialStatus?.active === false)

  // --- Simulate Trial Exhausted ---
  const simulateTrialMutation = useMutation({
    mutationFn: async () => {
      const result = await simulateTrialExhausted()
      if (!result.success) {
        throw new Error(
          result.message || 'Failed to simulate trial exhaustion.'
        )
      }
      return {
        ...result,
        message: result.message || 'Trial has been marked as exhausted.',
      }
    },
    onSuccess: () => {
      void invalidateTrialRelatedQueries(queryClient)
    },
  })

  const simulationMessage = simulateTrialMutation.data?.message ?? null
  const simulationError =
    simulateTrialMutation.error instanceof Error
      ? simulateTrialMutation.error.message
      : null

  const handleSimulateExhausted = () => {
    if (!isTrialSource) return
    simulateTrialMutation.reset()
    simulateTrialMutation.mutate()
  }

  // --- Clear Keyring ---
  const clearKeyringMutation = useMutation({
    mutationFn: clearKeyring,
    onSuccess: () => {
      window.location.reload()
    },
  })

  const clearKeyringError =
    clearKeyringMutation.error instanceof Error
      ? clearKeyringMutation.error.message
      : null

  const handleClearKeyring = () => {
    clearKeyringMutation.reset()
    clearKeyringMutation.mutate()
  }

  return (
    <VStack className="gap-3 items-stretch w-full">
      <Show when={isTrialSource && !isTrialExhausted}>
        <div className="rounded-xl border border-border-layout-1 p-4">
          <VStack className="gap-2 items-start">
            <HStack className="gap-3 items-center">
              <div className="w-9 h-9 rounded-xl bg-surface-negative-soft flex items-center justify-center">
                <Icon
                  name="alert"
                  label="Trial"
                  className="w-4 h-4 text-content-negative-soft"
                />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="label-small" className="text-content-layout-1">
                  Simulate Trial Exhausted
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Marks the current trial as exhausted for testing the exhausted
                  state UI.
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
              label="Simulate exhausted"
              icon="alert"
              iconPosition="left"
              onClick={handleSimulateExhausted}
              loading={simulateTrialMutation.isPending}
              disabled={simulateTrialMutation.isPending}
            />
          </VStack>
        </div>
      </Show>

      <div className="rounded-xl border border-border-layout-1 p-4">
        <VStack className="gap-2 items-start">
          <HStack className="gap-3 items-center">
            <div className="w-9 h-9 rounded-xl bg-surface-negative-soft flex items-center justify-center">
              <Icon
                name="trash"
                label="Clear Keyring"
                className="w-4 h-4 text-content-negative-soft"
              />
            </div>
            <VStack className="gap-0.5 items-start">
              <Text level="label-small" className="text-content-layout-1">
                Clear Keyring
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Removes stored secrets from the OS keychain, clears local trial
                state, and reloads the page.
              </Text>
            </VStack>
          </HStack>
          <Button
            variant="negative"
            modifier="outline"
            label="Clear keyring"
            icon="trash"
            iconPosition="left"
            onClick={() => setClearKeyringOpen(true)}
            loading={clearKeyringMutation.isPending}
            disabled={clearKeyringMutation.isPending}
          />
        </VStack>
      </div>

      <ConfirmDialog
        isOpen={clearKeyringOpen}
        onClose={() => setClearKeyringOpen(false)}
        onConfirm={handleClearKeyring}
        title="Clear the RDST keyring?"
        subtitle="Development environment"
        confirmLabel="Clear keyring"
        confirmVariant="negative"
        confirmIcon="trash"
        loading={clearKeyringMutation.isPending}
        blockCloseWhileLoading
        notice={{
          accent: 'negative',
          icon: 'trash',
          title: 'Stored secrets will be removed',
          message:
            'This removes RDST secrets from the OS keychain, clears local trial state, and reloads the app.',
        }}
      >
        {clearKeyringError ? (
          <Text level="body-small" className="text-content-negative-soft">
            {clearKeyringError}
          </Text>
        ) : null}
      </ConfirmDialog>
    </VStack>
  )
}
