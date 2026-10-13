import { Alert } from '@rs/ui-new/alert'
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'
import { useMutation } from '@tanstack/react-query'
import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import type { EnvRequirement } from '../lib/api'
import { setEnvSecret } from '../lib/api'
import { DialogShell } from './DialogShell'

interface EnvSecretsDialogProps {
  isOpen: boolean
  onClose: () => void
  requirements: EnvRequirement[]
  keyringAvailable: boolean
  onSuccess?: () => void
  onTrialRegister?: () => void
  // Label for the Readyset account pivot action.
  trialActionLabel?: string
  showManualAnthropicInput?: boolean
}

interface MissingEntry {
  key: string
  envName: string
  label: string
  hint: string
}

const maskedSecretStyle = { WebkitTextSecurity: 'disc' } as CSSProperties

function toMissingEntries(requirements: EnvRequirement[]): MissingEntry[] {
  return requirements.map((item) => {
    const envName = item.accepted_names[0] || ''
    const label =
      item.kind === 'target_password'
        ? `Password${item.target ? ` (${item.target})` : ''}`
        : 'Anthropic API key'
    const hint =
      item.kind === 'target_password'
        ? `Enter the password${item.target ? ` for ${item.target}` : ''}.`
        : 'Paste your Anthropic API key.'
    return {
      key: `${item.kind}:${envName}:${item.target || 'global'}`,
      envName,
      label,
      hint,
    }
  })
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
    const missingEntries = toMissingEntries(requirements)
    if (missingEntries.length > 0 || !showManualAnthropicInput) {
      return missingEntries
    }
    return [
      {
        key: 'anthropic_api_key:ANTHROPIC_API_KEY:global',
        envName: 'ANTHROPIC_API_KEY',
        label: 'Anthropic API key',
        hint: 'Paste your Anthropic API key.',
      },
    ]
  }, [requirements, showManualAnthropicInput])
  // When the dialog is only asking for the AI key, its title matches the
  // trigger ("Update Anthropic API key") instead of the generic "Set Required
  // Secrets" — "Required" is wrong once a key is already configured.
  // (configure-settings Copy #1)
  const isAnthropicOnly =
    entries.length > 0 &&
    entries.every((e) => e.key.startsWith('anthropic_api_key'))
  const hasTargetPasswordEntries = entries.some((entry) =>
    entry.key.startsWith('target_password:')
  )
  const dialogTitle = isAnthropicOnly
    ? 'Update Anthropic API key'
    : 'Set required secrets'
  const anthropicProcessEnvShadow =
    isAnthropicOnly &&
    requirements.some(
      (r) => r.kind === 'anthropic_api_key' && r.source === 'process_env'
    )
  const [values, setValues] = useState<Record<string, string>>({})
  const [persist, setPersist] = useState(true)
  const [validationError, setValidationError] = useState<string | null>(null)
  const wasOpenRef = useRef(false)
  type EnvSecretPayload = { name: string; value: string; persist: boolean }
  const setEnvSecretMutation = useMutation({
    mutationFn: async (payloads: EnvSecretPayload[]) => {
      let resolvedResultMessage: string | null = null

      for (const payload of payloads) {
        const response = await setEnvSecret(payload)
        if (!response.success) {
          throw new Error(response.message || 'Could not save this secret.')
        }
        if (response.message) {
          resolvedResultMessage = response.message
        } else if (response.session_only) {
          resolvedResultMessage = 'Saved for this session only.'
        }
      }

      return {
        resultMessage:
          resolvedResultMessage ??
          (payloads.length > 0 && payloads[0].persist && keyringAvailable
            ? 'Secrets saved securely and applied.'
            : 'Secrets applied to this RDST web session.'),
      }
    },
    onSuccess: () => {
      onSuccess?.()
      onClose()
    },
  })
  const mutationError =
    setEnvSecretMutation.error instanceof Error
      ? setEnvSecretMutation.error.message
      : null
  const errorMessage = validationError ?? mutationError
  const resultMessage = setEnvSecretMutation.data?.resultMessage ?? null

  useEffect(() => {
    if (isOpen && !wasOpenRef.current) {
      const nextValues: Record<string, string> = {}
      for (const entry of entries) {
        nextValues[entry.envName] = ''
      }
      setValues(nextValues)
      setPersist(true)
      setValidationError(null)
      setEnvSecretMutation.reset()
    }
    wasOpenRef.current = isOpen
  }, [isOpen, entries])

  const handleSubmit = () => {
    const payloads = entries
      .map((entry) => {
        const value = (values[entry.envName] || '').trim()
        return { name: entry.envName, value, persist }
      })
      .filter((item) => item.value.length > 0)

    if (payloads.length === 0) {
      setValidationError('Enter at least one secret value before saving.')
      return
    }

    setValidationError(null)
    setEnvSecretMutation.reset()
    setEnvSecretMutation.mutate(payloads)
  }

  return (
    <DialogShell
      open={isOpen}
      onClose={onClose}
      title={dialogTitle}
      description="Enter the missing passwords or API keys."
      note="Secrets are masked and never shown after submission."
      action={
        <>
          <Button
            variant="primary"
            modifier="ghost"
            label="Cancel"
            onClick={onClose}
            disabled={setEnvSecretMutation.isPending}
          />
          <Button
            variant="primary"
            modifier="solid"
            label="Save secrets"
            onClick={handleSubmit}
            loading={setEnvSecretMutation.isPending}
            disabled={entries.length === 0 || setEnvSecretMutation.isPending}
          />
        </>
      }
    >
      <div className="space-y-4">
        {anthropicProcessEnvShadow && (
          <Alert
            tone="quiet"
            variant="warning"
            icon="alert"
            label="A key supplied when RDST started will override this until the app restarts."
          />
        )}
        {!keyringAvailable && (
          <Alert
            tone="quiet"
            variant="warning"
            icon="alert"
            label={
              hasTargetPasswordEntries
                ? 'No OS keychain found. Re-enter the password after RDST restarts.'
                : 'No OS keychain found. Re-enter this value after RDST restarts.'
            }
          />
        )}
        {errorMessage && (
          <Alert
            tone="quiet"
            variant="negative"
            icon="alert"
            label={errorMessage}
          />
        )}

        {entries.length === 0 ? (
          <Text level="body-small" className="text-content-layout-2">
            No missing secrets.
          </Text>
        ) : (
          entries.map((entry, index) => (
            <div key={entry.key} className="space-y-1.5">
              <label htmlFor={`rdst-secret-${index}`} className="block">
                <Text
                  as="span"
                  level="caption"
                  className="text-content-layout-3"
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
          ))
        )}

        {keyringAvailable && (
          <div className="flex items-center justify-between gap-4">
            <div>
              <Text level="body-small" className="text-content-layout-1">
                Save securely
              </Text>
              <Text level="caption" className="text-content-layout-3">
                Persist in OS keychain when available.
              </Text>
            </div>
            <BaseInputSwitch
              name="persist"
              checked={persist}
              onCheckedChange={setPersist}
              disabled={setEnvSecretMutation.isPending}
            />
          </div>
        )}

        {resultMessage && (
          <Alert
            tone="quiet"
            variant="positive"
            icon="tick"
            label={resultMessage}
          />
        )}

        {onTrialRegister &&
          (requirements.some((r) => r.kind === 'anthropic_api_key') ||
            showManualAnthropicInput) && (
            <Button
              variant="primary"
              modifier="ghost"
              size="small"
              className="-ml-3"
              label={
                trialActionLabel ?? "Don't have a key? Sign in to Readyset"
              }
              onClick={() => {
                onClose()
                onTrialRegister()
              }}
            />
          )}
      </div>
    </DialogShell>
  )
}
