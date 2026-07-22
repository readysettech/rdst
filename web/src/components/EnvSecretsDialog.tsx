import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal';
import { Text } from '@rs/ui-new/text';
import { Button } from '@rs/ui-new/button';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { Alert } from '@rs/ui-new/alert';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Icon } from '@rs/ui-new/icon';
import type { EnvRequirement } from '../lib/api';
import { setEnvSecret } from '../lib/api';

interface EnvSecretsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  requirements: EnvRequirement[];
  keyringAvailable: boolean;
  onSuccess?: () => void;
  onTrialRegister?: () => void;
  // Label for the trial pivot action; callers vary it by trial state (e.g.
  // "Email me my trial token" when a trial is already the active source).
  trialActionLabel?: string;
  showManualAnthropicInput?: boolean;
}

// Trial tokens are UUIDs; Anthropic keys are sk-ant-... strings. The one key
// input accepts both and files each under the right name so users never deal
// with environment variable names themselves.
const TRIAL_TOKEN_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface MissingEntry {
  key: string;
  envName: string;
  label: string;
  hint: string;
}

const maskedSecretStyle = { WebkitTextSecurity: 'disc' } as CSSProperties;

function toMissingEntries(requirements: EnvRequirement[]): MissingEntry[] {
  return requirements.map((item) => {
      const envName = item.accepted_names[0] || '';
      const label =
        item.kind === 'target_password'
          ? `Password${item.target ? ` (${item.target})` : ''}`
          : 'Anthropic API Key';
      const hint =
        item.kind === 'target_password'
          ? `Set ${envName} for database authentication.`
          : 'Paste your Anthropic API key or your Readyset trial token.';
      return {
        key: `${item.kind}:${envName}:${item.target || 'global'}`,
        envName,
        label,
        hint,
      };
    });
}

export function EnvSecretsDialog({
  isOpen,
  onClose,
  requirements,
  keyringAvailable,
  onSuccess,
  onTrialRegister,
  trialActionLabel,
  showManualAnthropicInput = false,
}: EnvSecretsDialogProps) {
  const entries = useMemo(() => {
    const missingEntries = toMissingEntries(requirements);
    if (missingEntries.length > 0 || !showManualAnthropicInput) {
      return missingEntries;
    }
    return [
      {
        key: "anthropic_api_key:ANTHROPIC_API_KEY:global",
        envName: "ANTHROPIC_API_KEY",
        label: "Anthropic API Key",
        hint: "Paste your Anthropic API key or your Readyset trial token.",
      },
    ];
  }, [requirements, showManualAnthropicInput]);
  // When the dialog is only asking for the AI key, its title matches the
  // trigger ("Update Anthropic API key") instead of the generic "Set Required
  // Secrets" — "Required" is wrong once a key is already configured.
  // (configure-settings Copy #1)
  const isAnthropicOnly =
    entries.length > 0 && entries.every((e) => e.key.startsWith("anthropic_api_key"));
  const dialogTitle = isAnthropicOnly ? "Update Anthropic API key" : "Set required secrets";
  const anthropicProcessEnvShadow =
    isAnthropicOnly &&
    requirements.some(
      (r) => r.kind === "anthropic_api_key" && r.source === "process_env",
    );
  const [values, setValues] = useState<Record<string, string>>({});
  const [persist, setPersist] = useState(true);
  const [validationError, setValidationError] = useState<string | null>(null);
  const wasOpenRef = useRef(false);
  type EnvSecretPayload = { name: string; value: string; persist: boolean };
  const setEnvSecretMutation = useMutation({
    mutationFn: async (payloads: EnvSecretPayload[]) => {
      let resolvedResultMessage: string | null = null;

      for (const payload of payloads) {
        const response = await setEnvSecret(payload);
        if (!response.success) {
          throw new Error(response.message || `Failed to set ${payload.name}`);
        }
        if (response.session_only) {
          resolvedResultMessage = response.message || 'Saved for this session only.';
        }
      }

      return {
        resultMessage:
          resolvedResultMessage ??
          (payloads.length > 0 && payloads[0].persist && keyringAvailable
            ? 'Secrets saved securely and applied.'
            : 'Secrets applied to this RDST web session.'),
      };
    },
    onSuccess: () => {
      onSuccess?.();
      onClose();
    },
  });
  const mutationError =
    setEnvSecretMutation.error instanceof Error
      ? setEnvSecretMutation.error.message
      : null;
  const errorMessage = validationError ?? mutationError;
  const resultMessage = setEnvSecretMutation.data?.resultMessage ?? null;

  useEffect(() => {
    if (isOpen && !wasOpenRef.current) {
      const nextValues: Record<string, string> = {};
      for (const entry of entries) {
        nextValues[entry.envName] = '';
      }
      setValues(nextValues);
      setPersist(true);
      setValidationError(null);
      setEnvSecretMutation.reset();
    }
    wasOpenRef.current = isOpen;
  }, [isOpen, entries]);

  const handleSubmit = () => {
    const payloads = entries
      .map((entry) => {
        const value = (values[entry.envName] || '').trim();
        const name =
          entry.envName === 'ANTHROPIC_API_KEY' && TRIAL_TOKEN_RE.test(value)
            ? 'RDST_TRIAL_TOKEN'
            : entry.envName;
        return { name, value, persist };
      })
      .filter((item) => item.value.length > 0);

    if (payloads.length === 0) {
      setValidationError('Enter at least one secret value before saving.');
      return;
    }

    setValidationError(null);
    setEnvSecretMutation.reset();
    setEnvSecretMutation.mutate(payloads);
  };

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="p-0 overflow-hidden">
          <ModalTitle className="sr-only">{dialogTitle}</ModalTitle>
          <ModalDescription className="sr-only">
            Enter missing environment variable values for database and AI access.
          </ModalDescription>
          <div className="px-6 py-5 border-b border-border-layout-1 bg-surface-layout-2">
            <HStack className="gap-3 items-center">
              <div className="w-10 h-10 rounded-xl bg-surface-warning-soft flex items-center justify-center">
                <Icon name="key" label="Secrets" className="w-5 h-5 text-content-warning-soft" />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="headline-4" className="text-content-layout-1">
                  {dialogTitle}
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Secrets are masked and never shown after submission.
                </Text>
              </VStack>
            </HStack>
          </div>
          {anthropicProcessEnvShadow && (
            <div className="px-6 pt-4">
              <Alert
                variant="warning"
                modifier="outline"
                label="A key set in RDST's process environment will override this until the app restarts."
              />
            </div>
          )}

          <div className="p-6 space-y-4">
            {!keyringAvailable && (
              <Alert
                variant="warning"
                modifier="outline"
                label="Secure keychain is unavailable. Values will be session-only."
              />
            )}

            {errorMessage && <Alert variant="negative" modifier="outline" label={errorMessage} />}

            {entries.length === 0 ? (
              <Alert variant="positive" modifier="outline" label="No missing secrets." />
            ) : (
              <div className="space-y-4">
                {entries.map((entry, index) => (
                  <div key={entry.key} className="space-y-1">
                    <label htmlFor={`rdst-secret-${index}`}>
                      <Text
                        level="label-small"
                        className="text-content-layout-2 block"
                      >
                        {entry.label}
                      </Text>
                    </label>
                  <BaseInputText
                      id={`rdst-secret-${index}`}
                      type="text"
                      name={`rdst-secret-${index}`}
                      autoComplete="off"
                      autoCapitalize="none"
                      autoCorrect="off"
                      spellCheck={false}
                      data-1p-ignore="true"
                      data-lpignore="true"
                      data-form-type="other"
                      inputMode="text"
                      style={maskedSecretStyle}
                      value={values[entry.envName] || ''}
                      onChange={(event) =>
                        setValues((previous) => ({
                          ...previous,
                          [entry.envName]: event.target.value,
                        }))
                      }
                      placeholder={`Enter ${entry.label}`}
                      disabled={setEnvSecretMutation.isPending}
                    />
                    <Text level="caption" className="text-content-layout-3">
                      {entry.hint}
                    </Text>
                  </div>
                ))}
              </div>
            )}

            {onTrialRegister &&
              (requirements.some((r) => r.kind === 'anthropic_api_key') ||
                showManualAnthropicInput) && (
              <div className="flex justify-center">
                <Button
                  variant="primary"
                  modifier="ghost"
                  icon="sparkles"
                  iconPosition="left"
                  label={trialActionLabel ?? "Don't have a key? Claim free trial credits"}
                  onClick={() => {
                    onClose();
                    onTrialRegister();
                  }}
                />
              </div>
            )}

            <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/60 px-4 py-3 border border-border-layout-1">
              <VStack className="gap-0.5 items-start">
                <Text level="label-small" className="text-content-layout-1">
                  Save securely
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Persist in OS keychain when available.
                </Text>
              </VStack>
              <BaseInputSwitch
                name="persist"
                checked={persist}
                onCheckedChange={setPersist}
                disabled={setEnvSecretMutation.isPending}
              />
            </div>

            {resultMessage && (
              <Alert variant="positive" modifier="outline" label={resultMessage} />
            )}
          </div>

          <div className="px-6 py-4 border-t border-border-layout-1 bg-surface-layout-2/40">
            <HStack className="justify-end gap-3 items-center w-full">
              <Button
                variant="primary"
                modifier="ghost"
                label="Cancel"
                onClick={onClose}
                disabled={setEnvSecretMutation.isPending}
              />
              <Button
                variant="rising"
                label="Save Secrets"
                icon="tick"
                iconPosition="right"
                onClick={handleSubmit}
                loading={setEnvSecretMutation.isPending}
                disabled={entries.length === 0 || setEnvSecretMutation.isPending}
              />
            </HStack>
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}
