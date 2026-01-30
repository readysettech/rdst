import { useQuery, useQueryClient } from '@tanstack/react-query';
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
import {
  EnvRequirement,
  fetchEnvRequirements,
  fetchInitStatus,
  fetchStatus,
} from '../lib/api';

interface WarningConfig {
  title: string;
  description: string;
  command: string;
  severity: 'error' | 'warning';
  actionLabel?: string;
  actionType?: 'open-env-dialog';
}

function getWarningConfig(
  missingAnthropicRequirements: EnvRequirement[]
): WarningConfig | null {
  if (missingAnthropicRequirements.length > 0) {
    const names = missingAnthropicRequirements
      .map((item) => item.accepted_names[0])
      .filter(Boolean)
      .join(', ');
    return {
      title: 'Missing Anthropic API Key',
      description: `Required API key is missing: ${names}. Set it in Web to enable AI analysis without restarting.`,
      command: 'export RDST_ANTHROPIC_API_KEY=<value>',
      severity: 'warning',
      actionLabel: 'Set',
      actionType: 'open-env-dialog',
    };
  }

  return null;
}

function ConfigBanner({
  config,
  onDismiss,
  onAction
}: {
  config: WarningConfig;
  onDismiss?: () => void;
  onAction?: () => void;
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

              {/* Command block */}
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

              {config.actionLabel && onAction && (
                <Button
                  variant="primary"
                  modifier="outline"
                  label={config.actionLabel}
                  icon="key"
                  iconPosition="left"
                  onClick={onAction}
                />
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

  const { data: status, isLoading, error } = useQuery({
    queryKey: ['status'],
    queryFn: fetchStatus,
    staleTime: 30000,
    retry: 1,
  });
  const { data: initStatus, isLoading: initLoading } = useQuery({
    queryKey: ['init-status'],
    queryFn: fetchInitStatus,
    staleTime: 30000,
    retry: 1,
  });
  const { data: envRequirements } = useQuery({
    queryKey: ['env-requirements'],
    queryFn: fetchEnvRequirements,
    staleTime: 30000,
    retry: 1,
  });

  const missingAnthropicRequirements =
    envRequirements?.requirements.filter(
      (item) => !item.satisfied && item.kind === 'anthropic_api_key'
    ) || [];

  useEffect(() => {
    if (isLoading || initLoading || !status || !initStatus) return;
    if (location.pathname === '/onboarding') return;
    if (initStatus.initialized === false || status.targets.length === 0) {
      navigate({ to: '/onboarding' });
    }
  }, [isLoading, initLoading, status, initStatus, location.pathname, navigate]);

  // Reset dismissed state when status changes
  useEffect(() => {
    const config = getWarningConfig(missingAnthropicRequirements);
    if (config?.title !== dismissed) {
      setDismissed(null);
    }
  }, [missingAnthropicRequirements, dismissed]);

  if (isLoading) {
    return null;
  }

  if (error) return null;

  if (!status) return null;

  const warningConfig = getWarningConfig(missingAnthropicRequirements);

  if (!warningConfig || dismissed === warningConfig.title) {
    return null;
  }

  const handleAction = () => {
    if (warningConfig.actionType === 'open-env-dialog') {
      setShowSecretsDialog(true);
    }
  };

  const handleSecretSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ['status'] });
    queryClient.invalidateQueries({ queryKey: ['init-status'] });
    queryClient.invalidateQueries({ queryKey: ['env-requirements'] });
  };

  return (
    <>
      <AnimatePresence mode="wait">
        <ConfigBanner
          config={warningConfig}
          onDismiss={() => setDismissed(warningConfig.title)}
          onAction={warningConfig.actionType ? handleAction : undefined}
        />
      </AnimatePresence>
      <EnvSecretsDialog
        isOpen={showSecretsDialog}
        onClose={() => setShowSecretsDialog(false)}
        requirements={missingAnthropicRequirements}
        keyringAvailable={envRequirements?.keyring_available ?? false}
        onSuccess={handleSecretSuccess}
      />
    </>
  );
}
