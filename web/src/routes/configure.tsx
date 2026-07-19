/**
 * Configure page - Manage database connection targets
 */

import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useState, useEffect, useMemo, useRef } from "react";
import { useQueryClient } from '@tanstack/react-query';
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
import type { AnthropicKeyValidation, EnvRequirement } from "../lib/api";
import { useAnthropicValidity } from "../lib/useAnthropicValidity";
import { useSystemStatus } from "../lib/useSystemStatus";
import {
  ConfigureForm,
  ConfigureTargetList,
  ConfigureConnectionTest,
} from "../components/configure";
import type { ConfigureFormData, ConfigureTargetDetail } from "../types/configure";
import { invalidateTrialRelatedQueries, useTrialSource } from "../lib/trialQueries";

// Deep-link params for the routable notices (configure-and-identity return
// trips): `edit` opens a connection's edit form, `section=ai` focuses the AI
// key card, and `returnTo` sends the user back to the feature after the fix.
// Parse-only — never throw here (keeps the app shell intact; B1 lesson).
type ConfigureSearch = {
  edit?: string;
  section?: "ai";
  returnTo?: string;
};

export const Route = createFileRoute("/configure")({
  validateSearch: (search: Record<string, unknown>): ConfigureSearch => ({
    edit: typeof search.edit === "string" ? search.edit : undefined,
    section: search.section === "ai" ? "ai" : undefined,
    returnTo: typeof search.returnTo === "string" ? search.returnTo : undefined,
  }),
  component: ConfigurePage,
});

function keyValidationVariant(
  v: AnthropicKeyValidation,
): "positive" | "negative" | "warning" {
  if (v.valid) return "positive";
  if (v.reason === "rejected") return "negative";
  return "warning";
}

function keyValidationLabel(v: AnthropicKeyValidation): string {
  if (v.valid) return "Key is valid — Anthropic accepted it.";
  switch (v.reason) {
    case "rejected":
      return "Key rejected by Anthropic. Update it with a valid key.";
    case "no_key":
      return "No Anthropic key is configured yet.";
    default:
      return "Couldn't reach Anthropic to verify the key. Check your connection and try again.";
  }
}

function ConfigurePage() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const search = Route.useSearch();
  const [showForm, setShowForm] = useState(false);
  const [editingTarget, setEditingTarget] = useState<ConfigureTargetDetail | null>(null);
  const [showAnthropicDialog, setShowAnthropicDialog] = useState(false);
  const deepLinkHandledRef = useRef(false);

  const {
    listTargets,
    getTarget,
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

  const { data: statusData } = useSystemStatus();
  const dataDirectory = statusData?.data_directory ?? null;

  const { envRequirements, anthropicRequirement, anthropicSource, isTrialSource: trialSourceDetected, trialStatus } = useTrialSource();

  const isTrialExhausted =
    trialSourceDetected && (trialStatus?.status === "exhausted" || trialStatus?.active === false);
  const showAnthropicAction = Boolean(anthropicRequirement);

  // Presence vs. validity: a saved key can still be stale/rejected. Probe only
  // when a key is actually present, and treat the pre-resolve window as a
  // neutral "Checking…" state rather than a false-green "Configured".
  // Ports the key-validity probe from CL 14060, adapted to our query hooks.
  const hasAnthropicKey =
    (Boolean(anthropicRequirement?.satisfied) || trialSourceDetected) && !isTrialExhausted;
  const keyValidityQuery = useAnthropicValidity(hasAnthropicKey);
  const keyValidity = keyValidityQuery.data;
  const keyChecking = hasAnthropicKey && keyValidityQuery.isFetching && !keyValidity;
  const keyRejected =
    keyValidity?.valid === false && keyValidity.reason === "rejected";

  const anthropicStatusTitle = isTrialExhausted
    ? "Trial Credits Exhausted"
    : keyRejected
      ? "Anthropic Key Rejected"
      : keyChecking
        ? "Checking Anthropic Key…"
        : trialSourceDetected
          ? "Trial Credits In Use"
          : anthropicRequirement?.satisfied
            ? "Anthropic API Key Configured"
            : "Anthropic API Key Missing";

  const anthropicStatusDescription = isTrialExhausted
    ? "Your trial has run out. Add your own Anthropic API key to continue using AI analysis."
    : keyRejected
      ? "Anthropic rejected this key. Update it with a valid key to keep AI analysis working."
      : keyChecking
        ? "Verifying the key with Anthropic…"
        : trialSourceDetected
          ? "You can add or update your Anthropic API key here so AI analysis keeps working."
          : anthropicRequirement?.satisfied
            ? "You can replace your Anthropic API key here."
            : "Set your own Anthropic API key to avoid interruptions and keep using AI analysis.";
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
  // Trigger copy matches the dialog it opens: "Update key" when a key/trial is
  // already in play, "Set key" on first setup. (configure-settings Copy #1)
  const anthropicButtonLabel = isTrialExhausted || trialSourceDetected || anthropicRequirement?.satisfied
    ? "Update key"
    : "Set key";

  // Load targets on mount
  useEffect(() => {
    listTargets();
  }, [listTargets]);

  // Deep-links from the routable notices: open a connection's edit form
  // (?edit=name) or focus the AI key card (?section=ai). Runs once.
  useEffect(() => {
    if (deepLinkHandledRef.current) return;
    if (search.edit) {
      deepLinkHandledRef.current = true;
      const editTarget = search.edit;
      void (async () => {
        const target = await getTarget(editTarget);
        if (target) {
          setEditingTarget(target);
          setShowForm(true);
        }
      })();
    } else if (search.section === "ai") {
      deepLinkHandledRef.current = true;
      setShowAnthropicDialog(true);
    }
  }, [search.edit, search.section, getTarget]);

  // After a fix reached via a routable notice, send the user back to the
  // feature they came from so the flow resumes in place. [USE-021, USE-077]
  const returnToFeature = () => {
    if (search.returnTo) {
      router.history.push(search.returnTo);
    }
  };

  const handleAddClick = () => {
    setEditingTarget(null);
    setShowForm(true);
  };

  const handleEditClick = async (targetName: string) => {
    const target = await getTarget(targetName);
    if (!target) {
      return;
    }
    setEditingTarget(target);
    setShowForm(true);
  };

  const handleFormSubmit = async (data: ConfigureFormData) => {
    try {
      if (editingTarget) {
        await updateTarget(editingTarget.name, data);
      } else {
        await addTarget(data);
      }
    } catch {
      return;
    }
    setShowForm(false);
    setEditingTarget(null);
    // If we arrived here to fix a connection, resume the feature we came from.
    returnToFeature();
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

  const handleTestKey = () => {
    void keyValidityQuery.refetch();
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
              <div className="w-9 h-9 rounded-xl bg-surface-info-soft flex items-center justify-center shrink-0">
                <Icon name="info" label="Storage" className="w-4 h-4 text-content-info-soft" />
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
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${keyRejected ? 'bg-surface-negative-soft' : 'bg-surface-warning-soft'}`}>
                  <Icon name="key" label="Anthropic" className={`w-4 h-4 ${keyRejected ? 'text-content-negative-soft' : 'text-content-warning-soft'}`} />
                </div>
                <VStack className="gap-0.5 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    {anthropicStatusTitle}
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    {anthropicStatusDescription}
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
                <Show when={hasAnthropicKey}>
                  <Button
                    variant="primary"
                    modifier="ghost"
                    label={keyValidityQuery.isFetching ? "Testing…" : "Test key"}
                    disabled={keyValidityQuery.isFetching}
                    onClick={handleTestKey}
                  />
                </Show>
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
            <Show when={Boolean(keyValidity) && !keyValidityQuery.isFetching}>
              {keyValidity ? (
                <Alert
                  variant={keyValidationVariant(keyValidity)}
                  modifier="outline"
                  label={keyValidationLabel(keyValidity)}
                />
              ) : null}
            </Show>
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

      {/* Connection Test Result — also render while a test is in flight so the
          loading card shows instead of a dead-looking button. [QW17] */}
      <Show when={!!connectionTestResult || state === "loading"}>
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
            onEdit={(target) => void handleEditClick(target.name)}
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
          // Key changed: drop the cached validity verdict and re-check so the
          // status line reflects the new key, not the old result.
          void queryClient.invalidateQueries({ queryKey: ["anthropic-validity"] });
          void keyValidityQuery.refetch();
          // If a routable "needs a key" notice sent us here, resume the feature.
          returnToFeature();
        }}
      />
    </div>
  );
}
