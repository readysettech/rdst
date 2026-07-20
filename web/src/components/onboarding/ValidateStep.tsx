import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Card } from "@rs/ui-new/card";
import { Spinner } from "@rs/ui-new/spinner";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import type { ValidationResult } from "../../types/onboarding";
import { EnvSecretsDialog } from "../EnvSecretsDialog";
import { TrialRegistrationDialog } from "../TrialRegistrationDialog";
import { type EnvRequirement } from "../../lib/api";
import { useEnvRequirements } from "../../lib/useEnvRequirements";
import { invalidateTrialRelatedQueries } from "../../lib/trialQueries";

interface ValidateStepProps {
  targetNames: string[];
  results: ValidationResult | null;
  onRun: () => void;
  onNext: () => void;
  onBack: () => void;
  isLoading?: boolean;
}

export function ValidateStep({
  targetNames,
  results,
  onRun,
  onNext,
  onBack,
  isLoading,
}: ValidateStepProps) {
  const queryClient = useQueryClient();
  const [showSecretsDialog, setShowSecretsDialog] = useState(false);
  const [showTrialDialog, setShowTrialDialog] = useState(false);
  const [dialogRequirements, setDialogRequirements] = useState<EnvRequirement[]>([]);

  const { data: envRequirements } = useEnvRequirements();

  const missingAnthropicRequirements =
    envRequirements?.requirements.filter(
      (item) => !item.satisfied && item.kind === "anthropic_api_key",
    ) ?? [];
  const anthropicRequirement = envRequirements?.requirements.find(
    (item) => item.kind === "anthropic_api_key",
  );
  const anthropicRequirementKnown = Boolean(anthropicRequirement);
  const anthropicMissing = Boolean(anthropicRequirement && !anthropicRequirement.satisfied);

  const getDialogData = (target: string) => {
    return envRequirements?.requirements.filter((i) => i.target === target) ?? [];
  };

  useEffect(() => {
    if (!results && targetNames.length > 0) {
      onRun();
    }
  }, [results, targetNames, onRun]);

  const allTargetsSuccessful = results?.target_results.every((r) => r.success) ?? false;
  const hasResults = results !== null;
  const isTrialSource = anthropicRequirement?.source === "trial";
  const isExhaustedSource = anthropicRequirement?.source === "trial_exhausted";
  const llmStatus: "connected" | "trial" | "exhausted" | "optional" | "pending" = !anthropicRequirementKnown
    ? "pending"
    : isExhaustedSource
      ? "exhausted"
      : anthropicMissing
        ? "optional"
        : isTrialSource
          ? "trial"
          : "connected";

  const llmStatusStyles = {
    connected: {
      badge: "bg-surface-positive-soft text-content-positive-soft",
      container: "bg-surface-positive-soft/10 border-border-positive-soft",
      icon: "tick",
      iconClass: "text-content-positive-soft",
      title: "Configured",
      body: "Anthropic API key is set and ready for AI analysis.",
      badgeLabel: "Connected",
    },
    trial: {
      badge: "bg-surface-primary-soft text-content-primary-soft",
      container: "bg-surface-primary-soft/10 border-border-primary-soft",
      icon: "sparkles",
      iconClass: "text-content-primary-soft",
      title: "Free Trial Active",
      body: "Using RDST trial credits for AI analysis.",
      badgeLabel: "Trial",
    },
    exhausted: {
      badge: "bg-surface-negative-soft text-content-negative-soft",
      container: "bg-surface-negative-soft/10 border-border-negative-soft",
      icon: "alert",
      iconClass: "text-content-negative-soft",
      title: "Trial Credits Exhausted",
      body: "Your free trial tokens have been used up. Set an Anthropic API key to continue.",
      badgeLabel: "Exhausted",
    },
    optional: {
      badge: "bg-surface-warning-soft text-content-warning-soft",
      container: "bg-surface-warning-soft/10 border-border-warning-soft",
      icon: "info",
      iconClass: "text-content-warning-soft",
      title: "API Key Required for AI",
      body: "Set an API key or start a free trial to enable AI analysis.",
      badgeLabel: "Setup Needed",
    },
    pending: {
      badge: "bg-surface-raised text-content-layout-2",
      container: "bg-surface-layout-2/50 border-border-layout-1",
      icon: "info",
      iconClass: "text-content-layout-3",
      title: "Checking setup",
      body: "Checking Anthropic API key status...",
      badgeLabel: "Pending",
    },
  } as const;
  const llmState = llmStatusStyles[llmStatus];
  const needsAnthropicSetup = anthropicMissing || isExhaustedSource;

  const openSecretsDialog = (requirements: EnvRequirement[]) => {
    if (requirements.length === 0) return;
    setDialogRequirements(requirements);
    setShowSecretsDialog(true);
  };

  return (
    <Card>
      {/* Header */}
      <div className="px-6 py-5 border-b border-border-layout-1">
        <HStack className="gap-4 items-center justify-between">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-surface-positive-soft flex items-center justify-center">
              <Icon
                name="tick-double"
                label="Validate"
                className="w-6 h-6 text-content-positive-soft"
              />
            </div>
            <VStack className="gap-0.5 items-start">
              <Text level="headline-3" className="text-content-layout-1">
                Validate Configuration
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Testing database connectivity and API access
              </Text>
            </VStack>
          </HStack>
          <Button
            variant="primary"
            modifier="outline"
            icon="play"
            iconPosition="left"
            label={results ? "Re-run" : "Run Tests"}
            onClick={onRun}
            disabled={isLoading || targetNames.length === 0}
            loading={isLoading}
          />
        </HStack>
      </div>

      {/* Content */}
      <div className="p-6 space-y-6">
        {/* No targets warning */}
        <AnimatePresence>
          {targetNames.length === 0 && (
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="py-8 flex flex-col items-center text-center"
            >
              <div className="w-14 h-14 rounded-2xl bg-surface-warning-soft flex items-center justify-center mb-4">
                <Icon name="alert" label="Warning" className="w-7 h-7 text-content-warning-soft" />
              </div>
              <Text level="subtitle-2" className="text-content-layout-1">
                No targets to validate
              </Text>
              <Text level="body-small" className="text-content-layout-3 mt-1">
                Go back and add at least one database target first.
              </Text>
            </m.div>
          )}
        </AnimatePresence>

        {/* Database Targets Results */}
        {targetNames.length > 0 && (
          <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/30 overflow-hidden">
            <div className="px-4 py-3 border-b border-border-layout-1">
              <HStack className="gap-3 items-center">
                <Icon name="database" label="Databases" className="w-5 h-5 text-content-layout-3" />
                <Text level="subtitle-2" className="text-content-layout-1">
                  Database Connections
                </Text>
                {hasResults && (
                  <span
                    className={`ml-auto px-2 py-0.5 rounded-full text-xs font-medium ${
                      allTargetsSuccessful
                        ? "bg-surface-positive-soft text-content-positive-soft"
                        : "bg-surface-negative-soft text-content-negative-soft"
                    }`}
                  >
                    {allTargetsSuccessful ? "All Passed" : "Issues Found"}
                  </span>
                )}
              </HStack>
            </div>
            <div className="p-4">
              {isLoading && !results ? (
                <div className="py-6 flex flex-col items-center gap-3">
                  <Spinner size="base" />
                  <Text level="body-small" className="text-content-layout-3">
                    Testing connections...
                  </Text>
                </div>
              ) : results ? (
                <div className="space-y-2">
                  {results.target_results.map((result, index) => {
                    const targetNeedsPassword = !result.success && getDialogData(result.name).length > 0;

                    return (
                      <m.div
                        key={result.name}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: index * 0.1 }}
                        className={`rounded-lg border px-4 py-3 ${
                          result.success
                            ? "bg-surface-positive-soft/10 border-border-positive-soft"
                            : "bg-surface-negative-soft/10 border-border-negative-soft"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <HStack className="min-w-0 flex-1 gap-3 items-start">
                            <Icon
                              name={result.success ? "tick" : "close"}
                              label={result.success ? "Success" : "Failed"}
                              className={`mt-0.5 w-4 h-4 shrink-0 ${result.success ? "text-content-positive-soft" : "text-content-negative-soft"}`}
                            />
                            <VStack className="min-w-0 gap-0 items-start">
                              <Text level="label-small" className="text-content-layout-1">
                                {result.name}
                              </Text>
                              {result.error && (
                                <Text
                                  level="caption"
                                  className="break-words text-content-negative-soft"
                                >
                                  {result.error}
                                </Text>
                              )}
                            </VStack>
                          </HStack>
                          <Text
                            level="label-small"
                            className={`shrink-0 ${
                              result.success
                                ? "text-content-positive-soft"
                                : "text-content-negative-soft"
                            }`}
                          >
                            {result.success ? "Connected" : "Failed"}
                          </Text>
                        </div>
                        {targetNeedsPassword && (
                          <div className="mt-3 flex flex-col items-start justify-between gap-3 rounded-lg border border-border-warning-soft bg-surface-warning-soft/10 px-4 py-3 tablet:flex-row tablet:items-center">
                            <VStack className="gap-0.5 items-start">
                              <Text level="label-small" className="text-content-warning-soft">
                                Database password required
                              </Text>
                              <Text level="body-small" className="text-content-layout-3">
                                Set required DB password env vars in this step to continue testing.
                              </Text>
                            </VStack>
                            <Button
                              variant="primary"
                              modifier="outline"
                              icon="key"
                              iconPosition="left"
                              label="Set"
                              onClick={() => openSecretsDialog(getDialogData(result.name))}
                            />
                          </div>
                        )}
                      </m.div>
                    );
                  })}
                </div>
              ) : (
                <div className="py-4 text-center space-y-1">
                  <Text level="body-small" className="text-content-layout-3">
                    Validation starts automatically when you open this step.
                  </Text>
                  <Text level="caption" className="text-content-layout-3">
                    If it doesn&apos;t start, click &quot;Run Tests&quot;.
                  </Text>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Anthropic API Status */}
        <div className="rounded-xl border border-border-layout-1 bg-surface-layout-2/30 overflow-hidden">
          <div className="px-4 py-3 border-b border-border-layout-1">
            <HStack className="gap-3 items-center">
              <Icon name="sparkles" label="AI" className="w-5 h-5 text-content-layout-3" />
              <Text level="subtitle-2" className="text-content-layout-1">
                AI Analysis (Anthropic)
              </Text>
              {hasResults && (
                <span
                  className={`ml-auto px-2 py-0.5 rounded-full text-xs font-medium ${llmState.badge}`}
                >
                  {llmState.badgeLabel}
                </span>
              )}
            </HStack>
          </div>
          <div className="p-4">
            <div
              className={`flex items-start justify-between gap-3 rounded-lg border px-4 py-3 ${llmState.container}`}
            >
              <HStack className="gap-3 items-start">
                <Icon
                  name={llmState.icon}
                  label="Status"
                  className={`w-4 h-4 mt-0.5 ${llmState.iconClass}`}
                />
                <VStack className="gap-1 items-start">
                  <Text level="label-small" className="text-content-layout-1">
                    {llmState.title}
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    {llmState.body}
                  </Text>
                </VStack>
              </HStack>
              {needsAnthropicSetup && (
                <HStack className="gap-2 items-center shrink-0">
                  {isExhaustedSource ? (
                    <Button
                      variant="primary"
                      modifier="outline"
                      icon="key"
                      iconPosition="left"
                      label="Set API Key"
                      onClick={() => openSecretsDialog(
                        envRequirements?.requirements.filter(
                          (item) => item.kind === "anthropic_api_key",
                        ) ?? []
                      )}
                    />
                  ) : (
                    <>
                      <Button
                        variant="rising"
                        icon="sparkles"
                        iconPosition="left"
                        label="Try Free Trial"
                        onClick={() => setShowTrialDialog(true)}
                      />
                      <Button
                        variant="primary"
                        modifier="outline"
                        icon="key"
                        iconPosition="left"
                        label="I Have a Key"
                        onClick={() => openSecretsDialog(missingAnthropicRequirements)}
                      />
                    </>
                  )}
                </HStack>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Footer with navigation */}
      <div className="px-6 py-4 border-t border-border-layout-1 bg-surface-layout-2/30">
        <HStack className="justify-between w-full items-center">
          <Button
            variant="primary"
            modifier="ghost"
            icon="chevron-left"
            iconPosition="left"
            label="Back"
            onClick={onBack}
            disabled={isLoading}
          />
          <Button
            variant="primary"
            icon="arrow-right"
            iconPosition="right"
            label="Continue"
            onClick={onNext}
            disabled={!results || isLoading}
          />
        </HStack>
      </div>
      <EnvSecretsDialog
        isOpen={showSecretsDialog}
        onClose={() => {
          setShowSecretsDialog(false);
          setDialogRequirements([]);
        }}
        requirements={dialogRequirements}
        keyringAvailable={envRequirements?.keyring_available ?? false}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient);
          onRun();
        }}
      />
      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          void invalidateTrialRelatedQueries(queryClient);
          setShowTrialDialog(false);
          onRun();
        }}
      />
    </Card>
  );
}
