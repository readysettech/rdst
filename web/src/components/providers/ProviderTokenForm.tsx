import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { VStack } from '@rs/ui-new/stack'
import { useState } from 'react'
import { secondsSince, trackEvent } from '../../lib/analytics'
import type { FleetProviderSlug } from '../../lib/useFleet'
import { providerErrorClass } from '../../lib/userErrorClass'
import { Field } from '../configure/Field'
import { panelActions } from './cardShell'
import {
  asProviderFailure,
  type ProviderLoginFailure,
} from './useProviderOAuthLogin'

/**
 * The pasted-credential path a provider offers instead of, or alongside,
 * browser sign-in: one masked field, one save, and the provider's own wording
 * for a credential it refused.
 */
export function ProviderTokenForm({
  provider,
  name,
  label,
  placeholder,
  caption,
  submitLabel,
  rejectedMessage,
  buttonSize,
  save,
  onSaved,
  onCancel,
  actions,
}: {
  provider: FleetProviderSlug
  name: string
  label: string
  placeholder: string
  /** Says what kind of credential this is, where the field alone won't. */
  caption?: string
  submitLabel: string
  /** Shown in place of the raw error when the provider rejected the value. */
  rejectedMessage: string
  buttonSize?: 'small'
  save: (token: string) => Promise<void>
  onSaved: () => Promise<unknown>
  onCancel?: () => void
  /** The host dialog's footer, where the form's buttons render. */
  actions?: HTMLElement | null
}) {
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<ProviderLoginFailure | null>(null)

  const submit = async () => {
    setSaving(true)
    setFailure(null)
    const startedAt = Date.now()
    const report = (ok: boolean, errorClass: string | null) =>
      trackEvent('provider_sign_in_finished', {
        provider,
        method: 'api_key',
        ok,
        error_class: errorClass,
        seconds: secondsSince(startedAt),
      })
    trackEvent('provider_action', { provider, action: 'save_key' })
    trackEvent('provider_sign_in_started', { provider, method: 'api_key' })
    let saved = false
    try {
      await save(value.trim())
      saved = true
      report(true, null)
      setValue('')
      await onSaved()
    } catch (caught) {
      setFailure(asProviderFailure(caught))
      if (!saved) report(false, providerErrorClass(caught))
    } finally {
      setSaving(false)
    }
  }

  return (
    <VStack className="gap-3 items-stretch">
      <Field
        label={label}
        name={name}
        type="password"
        value={value}
        placeholder={placeholder}
        helper={caption}
        disabled={saving}
        onChange={setValue}
      />
      {failure && (
        <Alert
          variant="negative"
          modifier="ghost"
          icon="alert"
          iconPosition="left"
          label={
            failure.code === 'invalid_token' ? rejectedMessage : failure.message
          }
        />
      )}
      {panelActions(
        actions,
        <>
          {onCancel && (
            <Button
              variant="primary"
              modifier="ghost"
              size={buttonSize}
              label="Cancel"
              onClick={() => {
                trackEvent('provider_action', { provider, action: 'cancel' })
                onCancel()
              }}
            />
          )}
          <Button
            variant="primary"
            modifier="solid"
            size={buttonSize}
            label={submitLabel}
            aria-label={submitLabel}
            loading={saving}
            disabled={!value.trim()}
            onClick={() => void submit()}
          />
        </>
      )}
    </VStack>
  )
}
