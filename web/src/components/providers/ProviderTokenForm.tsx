import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
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
  name,
  placeholder,
  caption,
  submitLabel,
  submitBusyLabel,
  rejectedMessage,
  buttonSize,
  save,
  onSaved,
  onCancel,
}: {
  name: string
  placeholder: string
  /** Says what kind of credential this is, where the field alone won't. */
  caption?: string
  submitLabel: string
  submitBusyLabel: string
  /** Shown in place of the raw error when the provider rejected the value. */
  rejectedMessage: string
  buttonSize?: 'small'
  save: (token: string) => Promise<void>
  onSaved: () => Promise<unknown>
  onCancel?: () => void
}) {
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<ProviderLoginFailure | null>(null)

  const submit = async () => {
    setSaving(true)
    setFailure(null)
    try {
      await save(value.trim())
      setValue('')
      await onSaved()
    } catch (caught) {
      setFailure(asProviderFailure(caught))
    } finally {
      setSaving(false)
    }
  }

  return (
    <VStack className="gap-2 items-stretch">
      {caption && (
        <Text level="caption" className="text-content-layout-3">
          {caption}
        </Text>
      )}
      <HStack className="gap-2 items-center">
        <div className="flex-1 max-w-96">
          <BaseInputText
            name={name}
            type="password"
            value={value}
            placeholder={placeholder}
            autoComplete="off"
            disabled={saving}
            onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
              setValue(event.target.value)
            }
          />
        </div>
        <Button
          variant="primary"
          modifier="solid"
          size={buttonSize}
          label={saving ? submitBusyLabel : submitLabel}
          loading={saving}
          disabled={saving || !value.trim()}
          onClick={() => void submit()}
        />
        {onCancel && (
          <Button
            type="button"
            size="small"
            modifier="link"
            label="Cancel"
            classMerge="h-auto p-0"
            onClick={onCancel}
          />
        )}
      </HStack>
      {failure && (
        <Text level="caption" className="text-content-negative-soft">
          {failure.code === 'invalid_token' ? rejectedMessage : failure.message}
        </Text>
      )}
    </VStack>
  )
}
