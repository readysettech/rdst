import { useEffect, useMemo, useState } from 'react';
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
}

interface MissingEntry {
  key: string;
  envName: string;
  label: string;
  hint: string;
}

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
          : `Set ${envName}. Also accepted: ${item.accepted_names.slice(1).join(', ') || 'none'}.`;
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
}: EnvSecretsDialogProps) {
  const entries = useMemo(() => toMissingEntries(requirements), [requirements]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [persist, setPersist] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultMessage, setResultMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const nextValues: Record<string, string> = {};
    for (const entry of entries) {
      nextValues[entry.envName] = '';
    }
    setValues(nextValues);
    setPersist(true);
    setError(null);
    setResultMessage(null);
  }, [isOpen, entries]);

  const handleSubmit = async () => {
    const payloads = entries
      .map((entry) => ({
        name: entry.envName,
        value: (values[entry.envName] || '').trim(),
      }))
      .filter((item) => item.value.length > 0);

    if (payloads.length === 0) {
      setError('Enter at least one secret value before saving.');
      return;
    }

    setLoading(true);
    setError(null);
    setResultMessage(null);

    try {
      for (const payload of payloads) {
        const response = await setEnvSecret({
          name: payload.name,
          value: payload.value,
          persist,
        });
        if (!response.success) {
          throw new Error(response.message || `Failed to set ${payload.name}`);
        }
        if (response.session_only) {
          setResultMessage(response.message || 'Saved for this session only.');
        }
      }

      if (!resultMessage) {
        setResultMessage(
          persist && keyringAvailable
            ? 'Secrets saved securely and applied.'
            : 'Secrets applied to this RDST web session.'
        );
      }
      onSuccess?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save secrets.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="p-0 overflow-hidden">
          <ModalTitle className="sr-only">Set Required Secrets</ModalTitle>
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
                  Set Required Secrets
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Secrets are masked and never shown after submission.
                </Text>
              </VStack>
            </HStack>
          </div>

          <div className="p-6 space-y-4">
            {!keyringAvailable && (
              <Alert
                variant="warning"
                modifier="outline"
                label="Secure keychain is unavailable. Values will be session-only."
              />
            )}

            {error && <Alert variant="negative" modifier="outline" label={error} />}

            {entries.length === 0 ? (
              <Alert variant="positive" modifier="outline" label="No missing secrets." />
            ) : (
              <div className="space-y-4">
                {entries.map((entry, index) => (
                  <div key={entry.key} className="space-y-1">
                    <Text as="label" level="label-small" className="text-content-layout-2 block">
                      {entry.label}
                    </Text>
                    <BaseInputText
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
                      style={{ WebkitTextSecurity: "disc" }}
                      value={values[entry.envName] || ''}
                      onChange={(event) =>
                        setValues((previous) => ({
                          ...previous,
                          [entry.envName]: event.target.value,
                        }))
                      }
                      placeholder={`Enter value for ${entry.envName}`}
                      disabled={loading}
                    />
                    <Text level="caption" className="text-content-layout-3">
                      {entry.hint}
                    </Text>
                  </div>
                ))}
              </div>
            )}

            {onTrialRegister && requirements.some((r) => r.kind === 'anthropic_api_key') && (
              <div className="text-center">
                <button
                  type="button"
                  className="text-sm text-content-primary-soft hover:underline cursor-pointer"
                  onClick={() => {
                    onClose();
                    onTrialRegister();
                  }}
                >
                  Don&apos;t have a key? Try for free
                </button>
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
                disabled={loading}
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
                disabled={loading}
              />
              <Button
                variant="rising"
                label="Save Secrets"
                icon="tick"
                iconPosition="right"
                onClick={handleSubmit}
                loading={loading}
                disabled={entries.length === 0 || loading}
              />
            </HStack>
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}
