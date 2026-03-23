/**
 * Configure page - Manage database connection targets
 */

import { createFileRoute } from "@tanstack/react-router";
import { useState, useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Show } from "@rs/ui-new/show";
import { Alert } from "@rs/ui-new/alert";
import { CopyButton } from "@rs/ui-new/copy-button";
import { m } from "@rs/ui-new/motion";
import { useConfigure } from "../lib/useConfigure";
import { EnvSecretsDialog } from "../components/EnvSecretsDialog";
import type { EnvRequirement } from "../lib/api";
import { fetchStatus } from "../lib/api";
import {
  ConfigureForm,
  ConfigureTargetList,
  ConfigureConnectionTest,
} from "../components/configure";
import type { ConfigureTarget, ConfigureFormData } from "../types/configure";
import { invalidateTrialRelatedQueries, useTrialSource } from "../lib/trialQueries";

export const Route = createFileRoute("/configure")({
  component: ConfigurePage,
});

function ConfigurePage() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingTarget, setEditingTarget] = useState<ConfigureTarget | null>(null);
  const [showAnthropicDialog, setShowAnthropicDialog] = useState(false);

  const {
    listTargets,
    addTarget,
    updateTarget,
    removeTarget,
    setDefaultTarget,
    testConnection,
    state,
    targets,
    connectionTestResult,
    error,
    loading,
  } = useConfigure();

  const { data: statusData } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    staleTime: 60_000,
  });
  const dataDirectory = statusData?.data_directory ?? null;

  const { envRequirements, anthropicRequirement, anthropicSource, isTrialSource: trialSourceDetected, trialStatus } = useTrialSource();

  const isTrialExhausted =
    trialSourceDetected && (trialStatus?.status === "exhausted" || trialStatus?.active === false);
  const showAnthropicAction = Boolean(anthropicRequirement);
  const anthropicDialogRequirements: EnvRequirement[] = useMemo(() => {
    if (!anthropicRequirement) {
      return [];
    }
    if (!anthropicRequirement.accepted_names || anthropicRequirement.accepted_names.length === 0) {
      return [
        {
          ...anthropicRequirement,
          accepted_names: ["ANTHROPIC_API_KEY", "RDST_TRIAL_TOKEN"],
        },
      ];
    }
    return [anthropicRequirement];
  }, [anthropicRequirement]);
  const anthropicButtonLabel = isTrialExhausted || trialSourceDetected || anthropicRequirement?.satisfied
    ? "Update Anthropic Key"
    : "Set Anthropic Key";

  // Load targets on mount
  useEffect(() => {
    listTargets();
  }, [listTargets]);

  const handleAddClick = () => {
    setEditingTarget(null);
    setShowForm(true);
  };

  const handleEditClick = (target: ConfigureTarget) => {
    setEditingTarget(target);
    setShowForm(true);
  };

  const handleFormSubmit = async (data: ConfigureFormData) => {
    if (editingTarget) {
      await updateTarget(editingTarget.name, data);
    } else {
      await addTarget(data);
    }
    setShowForm(false);
    setEditingTarget(null);
  };

  const handleFormCancel = () => {
    setShowForm(false);
    setEditingTarget(null);
  };

  const handleDelete = async (targetName: string) => {
    if (confirm(`Are you sure you want to delete target "${targetName}"?`)) {
      await removeTarget(targetName);
    }
  };

  const handleSetDefault = async (targetName: string) => {
    await setDefaultTarget(targetName);
  };

  const handleTest = (targetName: string) => {
    testConnection(targetName);
  };

  const editingInitialData = editingTarget
    ? {
        name: editingTarget.name,
        engine: editingTarget.engine,
        host: editingTarget.host,
        port: editingTarget.port,
        database: editingTarget.database,
        user: editingTarget.user,
        password_env: editingTarget.password_env,
        tls: editingTarget.tls,
        read_only: editingTarget.read_only,
      }
    : undefined;

  return (
    <div className="space-y-6 w-full">
      {/* Header */}
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-positive-soft flex items-center justify-center">
              <Icon name="settings" label="Configure" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Database Targets
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Manage database connection profiles for RDST
              </Text>
            </VStack>
          </HStack>

          <Show when={!showForm}>
            <Button
              variant="rising"
              modifier="solid"
              icon="add"
              iconPosition="left"
              label="Add Target"
              onClick={handleAddClick}
            />
          </Show>
        </HStack>
      </m.div>

      {/* Data Storage Info */}
      <Show when={!!dataDirectory}>
        <m.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4">
            <HStack className="gap-3 items-start">
              <div className="w-9 h-9 rounded-xl bg-surface-informative-soft flex items-center justify-center shrink-0">
                <Icon name="info" label="Storage" className="w-4 h-4 text-content-informative-soft" />
              </div>
              <VStack className="gap-1 items-start flex-1 min-w-0">
                <Text level="label-small" className="text-content-layout-1">
                  Data Storage
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  All RDST data is stored locally on your machine. Nothing is sent to external servers.
                </Text>
                <HStack className="gap-2 items-center mt-1">
                  <code className="text-xs bg-surface-layout-3 px-2 py-1 rounded font-mono text-content-layout-2">
                    {dataDirectory}
                  </code>
                  <CopyButton text={dataDirectory || ""} />
                </HStack>
                <Text level="caption" className="text-content-layout-3 mt-0.5">
                  Contains connection configs, saved queries, semantic layer, and analysis history. Passwords are stored in your system keyring, never in plain text.
                </Text>
              </VStack>
            </HStack>
          </div>
        </m.div>
      </Show>

      <Show when={showAnthropicAction}>
        <m.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/50 p-4 space-y-3">
            <HStack className="items-start gap-3 justify-between">
              <HStack className="gap-3 items-start">
                <div className="w-9 h-9 rounded-xl bg-surface-warning-soft flex items-center justify-center">
                  <Icon name="key" label="Anthropic" className="w-4 h-4 text-content-warning-soft" />
                </div>
                <VStack className="gap-0.5 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    {isTrialExhausted
                      ? 'Trial Credits Exhausted'
                      : trialSourceDetected
                        ? 'Trial Credits In Use'
                        : anthropicRequirement?.satisfied
                          ? 'Anthropic API Key Configured'
                          : 'Anthropic API Key Missing'}
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    {isTrialExhausted
                      ? "Your trial has run out. Add your own Anthropic API key to continue using AI analysis."
                      : trialSourceDetected
                        ? "You can add or update your Anthropic API key here so AI analysis keeps working."
                        : anthropicRequirement?.satisfied
                          ? "You can replace your Anthropic API key here."
                          : "Set your own Anthropic API key to avoid interruptions and keep using AI analysis."}
                  </Text>
                  {anthropicSource === "process_env" && anthropicRequirement?.source ? (
                    <Text level="caption" className="text-content-layout-3">
                      Note: if ANTHROPIC_API_KEY is also set in the RDST web process environment, that value is used by default.
                    </Text>
                  ) : null}
                  {trialSourceDetected && trialStatus?.remaining_tokens_display && trialStatus?.limit_tokens_display ? (
                    <Text level="caption" className="text-content-layout-3">
                      Trial balance: {trialStatus.remaining_tokens_display} / {trialStatus.limit_tokens_display}
                    </Text>
                  ) : null}
                </VStack>
              </HStack>
              <HStack className="gap-2 items-center">
                <Button
                  variant="primary"
                  modifier="outline"
                  label={anthropicButtonLabel}
                  icon="key"
                  iconPosition="left"
                  onClick={() => setShowAnthropicDialog(true)}
                />
              </HStack>
            </HStack>
          </div>
        </m.div>
      </Show>

      {/* Error Display */}
      <Show when={!!error}>
        <m.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <Alert
            variant="negative"
            modifier="outline"
            label={`Error: ${error}`}
          />
        </m.div>
      </Show>

      {/* Form */}
      <Show when={showForm}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <ConfigureForm
            key={editingTarget ? `edit-${editingTarget.name}` : 'new-target'}
            initialData={editingInitialData}
            onSubmit={handleFormSubmit}
            onCancel={handleFormCancel}
            isLoading={loading}
          />
        </m.div>
      </Show>

      {/* Connection Test Result */}
      <Show when={!!connectionTestResult}>
        <m.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <ConfigureConnectionTest result={connectionTestResult} isLoading={state === "loading"} />
        </m.div>
      </Show>

      {/* Target List */}
      <Show when={!showForm}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
        >
          <ConfigureTargetList
            targets={targets}
            onEdit={handleEditClick}
            onTest={handleTest}
            onDelete={handleDelete}
            onSetDefault={handleSetDefault}
            isLoading={loading}
          />
        </m.div>
      </Show>

      <EnvSecretsDialog
        isOpen={showAnthropicDialog}
        onClose={() => setShowAnthropicDialog(false)}
        requirements={anthropicDialogRequirements}
        showManualAnthropicInput
        keyringAvailable={Boolean(envRequirements?.keyring_available)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient);
        }}
      />
    </div>
  );
}
