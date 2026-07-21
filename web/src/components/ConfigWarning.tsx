import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from '@tanstack/react-router';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { getTransition } from '@rs/ui-new/transition';
import { Icon } from '@rs/ui-new/icon';
import { Text } from '@rs/ui-new/text';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Button } from '@rs/ui-new/button';
import { CopyButton } from '@rs/ui-new/copy-button';
import { EnvSecretsDialog } from './EnvSecretsDialog';
import { type EnvRequirement } from '../lib/api';
import { useInitStatus } from '../lib/useInitStatus';
import { useSystemStatus } from '../lib/useSystemStatus';
import { TrialRegistrationDialog } from './TrialRegistrationDialog';
import { invalidateTrialRelatedQueries, useTrialSource } from '../lib/trialQueries';

interface WarningConfig {
  title: string;
  description: string;
  command?: string;
  severity: 'error' | 'warning';
  actionLabel?: string;
  actionType?: 'open-env-dialog' | 'open-trial-dialog';
  secondaryActionLabel?: string;
  secondaryActionType?: 'open-trial-dialog' | 'open-env-dialog';
}

interface TrialState {
  active: boolean;
  percent_remaining?: number;
  remaining_tokens_display?: string;
  limit_tokens_display?: string;
}

function getWarningConfig(
  missingAnthropicRequirements: EnvRequirement[],
  trialState?: TrialState,
): WarningConfig | null {
  // Trial exhausted — needs a real key
  if (trialState?.active && trialState.percent_remaining != null && trialState.percent_remaining <= 0) {
    return {
      title: 'Trial Credits Exhausted',
      description: 'Your free trial tokens have been used up. Set an Anthropic API key to continue using AI analysis.',
      severity: 'error',
      actionLabel: 'Set API Key',
      actionType: 'open-env-dialog',
    };
  }

  // Trial low balance
  if (trialState?.active && trialState.percent_remaining != null && trialState.percent_remaining < 25) {
    return {
      title: 'Low Trial Balance',
      description: `${trialState.remaining_tokens_display} of ${trialState.limit_tokens_display} trial tokens remaining. Consider getting your own API key.`,
      severity: 'warning',
      actionLabel: 'Set API Key',
      actionType: 'open-env-dialog',
    };
  }

  // Missing key — offer both trial and set key
  if (missingAnthropicRequirements.length > 0) {
    return {
      title: 'Missing Anthropic API Key',
      description:
        "AI-powered features require an Anthropic API key. Set your key below, or claim free trial credits if you don't have one.",
      severity: 'warning',
      actionLabel: 'Claim free trial credits',
      actionType: 'open-trial-dialog',
      secondaryActionLabel: 'Set API key',
      secondaryActionType: 'open-env-dialog',
    };
  }

  return null;
}

function ConfigBanner({
  config,
  onDismiss,
  onAction,
  onSecondaryAction,
}: {
  config: WarningConfig;
  onDismiss?: () => void;
  onAction?: () => void;
  onSecondaryAction?: () => void;
}) {
  const isError = config.severity === 'error';

  return (
    <m.div
      initial={{ opacity: 0, y: -8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.98 }}
      transition={getTransition('springFast')}
      className={`
        relative overflow-hidden rounded-2xl mb-4
        border-(length:--border-base)
        ${isError
          ? 'bg-surface-negative-soft border-border-negative-soft'
          : 'bg-surface-warning-soft border-border-warning-soft'
        }
      `}
    >
      {/* Subtle gradient accent line at top */}
      <div
        className={`
          absolute top-0 left-0 right-0 h-[2px]
          ${isError
            ? 'bg-gradient-to-r from-transparent via-content-negative-soft to-transparent'
            : 'bg-gradient-to-r from-transparent via-content-warning-soft to-transparent'
          }
        `}
      />

      <div className="px-4 py-3">
        <HStack className="gap-3 items-start justify-between">
          {/* Left: Icon + Content */}
          <HStack className="gap-3 items-start flex-1 min-w-0">
            {/* Icon container */}
            <div className={`
              flex-shrink-0 mt-0.5 p-1.5 rounded-lg
              ${isError ? 'bg-surface-negative-soft' : 'bg-surface-warning-soft'}
            `}>
              <Icon
                name="alert"
                label="Warning"
                className={`
                  w-4 h-4
                  ${isError ? 'text-content-negative-soft' : 'text-content-warning-soft'}
                `}
              />
            </div>

            {/* Content */}
            <VStack className="gap-2 items-start flex-1 min-w-0">
              {/* Title + Description */}
              <VStack className="gap-0.5 items-start">
                <Text
                  level="label-small"
                  className={isError ? 'text-content-negative-soft' : 'text-content-warning-soft'}
                >
                  {config.title}
                </Text>
                <Text
                  level="body-small"
                  className="text-content-layout-2"
                >
                  {config.description}
                </Text>
              </VStack>

              {config.command && (
                <HStack className="
                  gap-2 items-center w-full
                  bg-surface-layout-2/50
                  rounded-lg px-3 py-2
                  border-(length:--border-base) border-border-layout-1
                ">
                  <Text
                    level="mono-small"
                    className="text-content-layout-1 flex-1 truncate select-all"
                  >
                    {config.command}
                  </Text>
                  <CopyButton text={config.command} />
                </HStack>
              )}

              {(config.actionLabel && onAction) && (
                <HStack className="gap-2 items-center">
                  <Button
                    variant="primary"
                    modifier="outline"
                    label={config.actionLabel}
                    icon={config.actionType === 'open-trial-dialog' ? 'sparkles' : 'key'}
                    iconPosition="left"
                    onClick={onAction}
                  />
                  {config.secondaryActionLabel && onSecondaryAction && (
                    <Button
                      variant="primary"
                      modifier="ghost"
                      label={config.secondaryActionLabel}
                      icon="key"
                      iconPosition="left"
                      onClick={onSecondaryAction}
                    />
                  )}
                </HStack>
              )}
            </VStack>
          </HStack>

          {/* Right: Dismiss button (only for warnings, not errors) */}
          {!isError && onDismiss && (
            <Button
              label=""
              icon="close"
              iconPosition="icon"
              modifier="ghost"
              size="small"
              onClick={onDismiss}
              className="flex-shrink-0 opacity-60 hover:opacity-100"
            />
          )}
        </HStack>
      </div>
    </m.div>
  );
}

export function ConfigWarning() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [showSecretsDialog, setShowSecretsDialog] = useState(false);
  const [showTrialDialog, setShowTrialDialog] = useState(false);

  const { data: status, isLoading, error } = useSystemStatus();
  const { data: initStatus, isLoading: initLoading } = useInitStatus();
  const { envRequirements, isTrialSource, trialStatus } = useTrialSource();

  const missingAnthropicRequirements =
    envRequirements?.requirements.filter(
      (item) => !item.satisfied && item.kind === 'anthropic_api_key'
    ) || [];

  const trialState: TrialState | undefined =
    isTrialSource && (trialStatus?.active || trialStatus?.status === 'exhausted')
      ? {
          active: true,
          percent_remaining: trialStatus.status === 'exhausted' ? 0 : (trialStatus.percent_remaining ?? undefined),
          remaining_tokens_display: trialStatus.remaining_tokens_display ?? undefined,
          limit_tokens_display: trialStatus.limit_tokens_display ?? undefined,
        }
      : undefined;

  useEffect(() => {
    if (isLoading || initLoading || !status || !initStatus) return;
    if (
      location.pathname === '/' ||
      location.pathname === '/onboarding' ||
      // Settings must stay reachable with zero targets: it's where the user
      // manages keys and can reset local data. Redirecting it away traps a
      // fresh or wiped install with no way back in.
      location.pathname === '/configure' ||
      location.pathname.startsWith('/demo')
    ) return;
    if (initStatus.initialized === false || status.targets.length === 0) {
      // Route to Connect preserving where the user was headed, so they land
      // back there after connecting (configure-and-identity open-dep #4).
      navigate({ to: '/onboarding', search: { redirect: location.pathname } });
    }
  }, [isLoading, initLoading, status, initStatus, location.pathname, navigate]);

  // Reset dismissed state when status changes
  useEffect(() => {
    const config = getWarningConfig(missingAnthropicRequirements, trialState);
    if (config?.title !== dismissed) {
      setDismissed(null);
    }
  }, [missingAnthropicRequirements, trialState, dismissed]);

  if (isLoading) {
    return null;
  }

  if (error) return null;

  if (!status) return null;

  const warningConfig = getWarningConfig(missingAnthropicRequirements, trialState);

  if (!warningConfig || dismissed === warningConfig.title) {
    return null;
  }

  // The banner only belongs on pages that use the key (Ask, Audit, ...).
  // The home page stays welcome-clean (the Ask card carries its own "needs a
  // key" chip), and the demo runs entirely without an Anthropic key.
  if (location.pathname === '/' || location.pathname.startsWith('/demo')) {
    return null;
  }

  const shouldShowManualAnthropicInput =
    warningConfig.actionType === 'open-env-dialog' ||
    warningConfig.secondaryActionType === 'open-env-dialog';

  const handleAction = () => {
    if (warningConfig.actionType === 'open-env-dialog') {
      setShowSecretsDialog(true);
    } else if (warningConfig.actionType === 'open-trial-dialog') {
      setShowTrialDialog(true);
    }
  };

  const handleSecondaryAction = () => {
    if (warningConfig.secondaryActionType === 'open-env-dialog') {
      setShowSecretsDialog(true);
    } else if (warningConfig.secondaryActionType === 'open-trial-dialog') {
      setShowTrialDialog(true);
    }
  };

  const invalidateAll = () => {
    void invalidateTrialRelatedQueries(queryClient);
  };

  return (
    <>
      <AnimatePresence mode="wait">
        <ConfigBanner
          config={warningConfig}
          onDismiss={() => setDismissed(warningConfig.title)}
          onAction={warningConfig.actionType ? handleAction : undefined}
          onSecondaryAction={warningConfig.secondaryActionType ? handleSecondaryAction : undefined}
        />
      </AnimatePresence>
      <EnvSecretsDialog
        isOpen={showSecretsDialog}
        onClose={() => setShowSecretsDialog(false)}
        requirements={missingAnthropicRequirements}
        showManualAnthropicInput={shouldShowManualAnthropicInput}
        keyringAvailable={envRequirements?.keyring_available ?? false}
        onSuccess={invalidateAll}
        onTrialRegister={() => setShowTrialDialog(true)}
      />
      <TrialRegistrationDialog
        isOpen={showTrialDialog}
        onClose={() => setShowTrialDialog(false)}
        onSuccess={() => {
          invalidateAll();
          setShowTrialDialog(false);
        }}
      />
    </>
  );
}
